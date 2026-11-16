import asyncio
import socket
import ipaddress
import time

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock

# Самые важные порты для идентификации устройств и сбора баннеров
TARGET_PORTS = {
    21: "FTP", 22: "SSH", 23: "Telnet", 80: "HTTP", 135: "RPC",
    139: "NetBIOS", 443: "HTTPS", 445: "SMB", 3389: "RDP", 8080: "HTTP-Alt"
}

class AsyncScannerLayout(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', padding=10, spacing=10, **kwargs)
        
        self.is_scanning = False
        self.scan_results = []
        self.loop = None

        # Панель управления
        control_layout = BoxLayout(orientation='horizontal', size_hint_y=None, height=50, spacing=5)
        
        self.ip_input = TextInput(text='', placeholder_text='192.168.1.0/24', multiline=False, font_size=16)
        control_layout.add_widget(self.ip_input)
        
        self.scan_btn = Button(text='СТАРТ (Асинхрон)', size_hint_x=None, width=160, background_color=(0.1, 0.5, 0.9, 1), font_size=14)
        self.scan_btn.bind(on_press=self.toggle_scan)
        control_layout.add_widget(self.scan_btn)
        
        self.add_widget(control_layout)

        # Индикаторы скорости
        self.progress_bar = ProgressBar(max=100, size_hint_y=None, height=15)
        self.add_widget(self.progress_bar)
        
        self.status_label = Label(text='Определение OTG / Wi-Fi сети...', size_hint_y=None, height=25, color=(0.8, 0.8, 0.8, 1), font_size=13)
        self.add_widget(self.status_label)

        # Вывод данных (Таблица)
        scroll = ScrollView()
        self.result_grid = GridLayout(cols=3, size_hint_y=None, spacing=3)
        self.result_grid.bind(minimum_height=self.result_grid.setter('height'))
        
        self._add_row("IP-Адрес", "Инфо / Имя", "Службы и Ответы (Banner)", is_header=True)
        scroll.add_widget(self.result_grid)
        self.add_widget(scroll)
        
        # Автоопределение рабочей подсети интерфейса (поддерживает OTG-Ethernet)
        Clock.schedule_once(self._detect_network_interface, 0.2)

    def _add_row(self, ip, info, banner, is_header=False):
        bg_color = (0.2, 0.7, 1, 1) if is_header else (1, 1, 1, 1)
        self.result_grid.add_widget(Label(text=ip, size_hint_y=None, height=35, color=bg_color, font_size=13, halign='left'))
        self.result_grid.add_widget(Label(text=info, size_hint_y=None, height=35, color=bg_color, font_size=13, halign='left'))
        self.result_grid.add_widget(Label(text=banner, size_hint_y=None, height=35, color=bg_color, font_size=13, halign='left'))

    def _detect_network_interface(self, dt):
        try:
            # Открываем сокет наружу, чтобы ОС подсказала IP текущего активного интерфейса (Wi-Fi или OTG-Ethernet)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            subnet = ".".join(local_ip.split(".")[:3]) + ".0/24"
            self.ip_input.text = subnet
            self.status_label.text = f"Интерфейс определен. IP: {local_ip}"
        except Exception:
            self.ip_input.text = "192.168.1.0/24"
            self.status_label.text = "Сеть не найдена. Введите диапазон вручную."

    def toggle_scan(self, instance):
        if self.is_scanning:
            self.is_scanning = False
            return
            
        self.is_scanning = True
        self.scan_btn.text = "СТОП"
        self.scan_btn.background_color = (0.9, 0.2, 0.2, 1)
        
        # Очищаем таблицу кроме шапки
        headers = self.result_grid.children[-3:] if len(self.result_grid.children) >= 3 else []
        self.result_grid.clear_widgets()
        for h in reversed(headers):
            self.result_grid.add_widget(h)
            
        self.scan_results.clear()
        
        # Запуск асинхронного движка в фоновом потоке, чтобы GUI телефона не зависал
        import threading
        threading.Thread(target=self._start_async_loop, daemon=True).start()

    def _start_async_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.network_engine())

    async def fetch_banner(self, ip, port, timeout=0.4):
        """Асинхронный сборщик баннеров (Banner Grabbing)"""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=timeout
            )
            # Если порт HTTP, запрашиваем заголовки для определения вендора
            if port in:
                writer.write(b"HEAD / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                await writer.drain()
            
            # Читаем первые 100 байт ответа устройства
            data = await asyncio.wait_for(reader.read(100), timeout=timeout)
            writer.close()
            await writer.wait_closed()
            
            response = data.decode('utf-8', errors='ignore').strip()
            if "Server:" in response:
                for line in response.split("\r\n"):
                    if line.startswith("Server:"):
                        return f"{TARGET_PORTS[port]} ({line[7:].strip()})"
            return TARGET_PORTS[port]
        except:
            return None

    async def scan_single_device(self, ip, semaphore):
        """Анализ одного IP адреса с ограничением конкурентности"""
        async with semaphore:
            if not self.is_scanning:
                return None
                
            open_services = []
            device_detected = False
            
            # 1. Параллельно опрашиваем все ключевые порты на этом IP
            tasks = [self.fetch_banner(str(ip), port) for port in TARGET_PORTS.keys()]
            results = await asyncio.gather(*tasks)
            
            for res in results:
                if res:
                    device_detected = True
                    open_services.append(res)

            # 2. Если порты закрыты, делаем быстрый низкоуровневый TCP-пинг на 80/443 порты
            if not device_detected:
                for port in:
                    try:
                        _, writer = await asyncio.wait_for(asyncio.open_connection(str(ip), port), timeout=0.15)
                        writer.close()
                        await writer.wait_closed()
                        device_detected = True
                        open_services.append(f"Активен (Порт {port})")
                        break
                    except:
                        continue

            if device_detected:
                # Пытаемся вытащить локальное сетевое имя (NetBIOS / DNS)
                try:
                    hostname = socket.gethostbyaddr(str(ip))[0]
                except:
                    hostname = "Устройство"

                services_str = ", ".join(open_services) if open_services else "Онлайн"
                res_dict = {"ip": str(ip), "host": hostname, "services": services_str}
                
                # Передаем данные в GUI поток Kivy без задержек
                Clock.schedule_once(lambda dt: self._add_row(res_dict["ip"], res_dict["host"], res_dict["services"]), 0)
                return res_dict
        return None

    async def network_engine(self):
        start_time = time.time()
        try:
            net = ipaddress.IPv4Network(self.ip_input.text.strip(), strict=False)
            hosts = list(net.hosts())
        except Exception as e:
            Clock.schedule_once(lambda dt: self._finish_ui("Ошибка формата сети"), 0)
            return

        total_hosts = len(hosts)
        completed = 0
        
        # Семафор контролирует одновременные запросы, защищая OTG-адаптер от перегрузки буфера сокетов
        semaphore = asyncio.Semaphore(300) 
        
        tasks = []
        for ip in hosts:
            tasks.append(self.scan_single_device(ip, semaphore))
            
        # Запускаем обработку всей сети
        for future in asyncio.as_completed(tasks):
            if not self.is_scanning:
                break
            await future
            completed += 1
            
            progress_val = int((completed / total_hosts) * 100)
            Clock.schedule_once(lambda dt, p=progress_val: setattr(self.progress_bar, 'value', p), 0)
            Clock.schedule_once(lambda dt, c=completed, t=total_hosts: setattr(
                self.status_label, 'text', f"Сканирование: {c}/{t} IP адресов..."
            ), 0)

        elapsed = round(time.time() - start_time, 2)
        Clock.schedule_once(lambda dt: self._finish_ui(f"Сканирование завершено за {elapsed} сек!"), 0)

    def _finish_ui(self, message):
        self.is_scanning = False
        self.scan_btn.text = "СТАРТ (Асинхрон)"
        self.scan_btn.background_color = (0.1, 0.5, 0.9, 1)
        self.status_label.text = message


class UltraNetPulseApp(App):
    def build(self):
        return AsyncScannerLayout()

if __name__ == '__main__':
    UltraNetPulseApp().run()
