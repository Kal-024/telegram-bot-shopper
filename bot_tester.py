"""
Bot Tester - Simulación y pruebas de carga del bot multi-tenant.

Simula el flujo completo del bot sin hacer peticiones reales a Telegram.
Cuenta las llamadas HTTP que haría el bot para estimar costos de hosting.

Variables de configuración al inicio del archivo:
- ITERACIONES: Número de ciclos completos a ejecutar
- USUARIOS_POR_EVENTO: Compradores simulados por evento
- PRODUCTOS_POR_EVENTO: Productos por evento
"""

import asyncio
import time
import os
import json
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock
from collections import defaultdict

# ─── Configuraciones de prueba ────────────────────────────────────────────

ITERACIONES = 1
USUARIOS_POR_EVENTO = 3
PRODUCTOS_POR_EVENTO = 2

# ─── Setup ────────────────────────────────────────────────────────────────

# Token falso para evitar conexiones reales
os.environ["BOT_TOKEN"] = "1234:fake_token_for_testing"

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ConversationHandler, CallbackQueryHandler
)

# Importar lógica principal refactorizada
from main import (
    ShopperBot, State, start, event_start, receive_media, receive_title,
    receive_datetime, send_event, resumen_start, receive_event_id_resumen,
    fin_start, receive_event_id_fin, consolidado_start,
    receive_event_id_consolidado, historial, limpiar_start,
    receive_event_id_limpiar, handle_mio_query, handle_channel_message, cancel
)


class BotSimulator:
    """Simulador que construye un ShopperBot mockeado y envía updates falsos."""

    def __init__(self, admin_id, channel_id):
        self.admin_id = admin_id
        self.channel_id = channel_id
        self.app = None
        self.msg_id_counter = 1000
        self.update_id_counter = 1
        self.mock_do_post = None

    async def initialize(self):
        """Construye el bot usando la clase ShopperBot con config de prueba."""
        bot = ShopperBot(
            bot_token=os.environ["BOT_TOKEN"],
            channel_id=self.channel_id,
            authorized_users=[self.admin_id],
            store_name="Tienda de Prueba",
            data_dir="data/test_simulation",
        )
        self.app = bot.build()
        await self.app.initialize()
        await self.app.start()

    async def stop(self):
        await self.app.stop()

    def generate_update_id(self):
        self.update_id_counter += 1
        return self.update_id_counter

    def generate_msg_id(self):
        self.msg_id_counter += 1
        return self.msg_id_counter

    async def send_mock_update(self, update_dict):
        update = Update.de_json(update_dict, self.app.bot)
        await self.app.process_update(update)
        await asyncio.sleep(0.01)  # Ceder control al loop

    async def cmd(self, user_id, command, text=""):
        """Envía un comando de bot simulado."""
        full_text = command if not text else f"{command} {text}"
        length = len(command)
        update_dict = {
            "update_id": self.generate_update_id(),
            "message": {
                "message_id": self.generate_msg_id(),
                "date": int(time.time()),
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": user_id, "first_name": f"User{user_id}", "username": f"user{user_id}", "is_bot": False},
                "text": full_text,
                "entities": [{"type": "bot_command", "offset": 0, "length": length}] if command.startswith('/') else []
            }
        }
        if not command.startswith('/'):
            del update_dict["message"]["entities"]

        await self.send_mock_update(update_dict)

    async def msg(self, user_id, text):
        """Envía un mensaje de texto simulado (no comando)."""
        await self.cmd(user_id, text)

    async def photo(self, user_id):
        """Envía una foto simulada."""
        update_dict = {
            "update_id": self.generate_update_id(),
            "message": {
                "message_id": self.generate_msg_id(),
                "date": int(time.time()),
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": user_id, "first_name": f"User{user_id}", "username": f"user{user_id}", "is_bot": False},
                "photo": [{"file_id": "test_photo_id", "file_unique_id": "test_photo_uid", "width": 100, "height": 100}]
            }
        }
        await self.send_mock_update(update_dict)

    async def callback_query(self, user_id, data):
        """Envía un callback_query simulado (pulsación de botón inline)."""
        update_dict = {
            "update_id": self.generate_update_id(),
            "callback_query": {
                "id": str(self.generate_msg_id()),
                "from": {"id": user_id, "first_name": f"User{user_id}", "username": f"user{user_id}", "is_bot": False},
                "message": {
                    "message_id": self.generate_msg_id(),
                    "date": int(time.time()),
                    "chat": {"id": self.channel_id, "type": "channel"},
                    "text": "Mensaje de prueba"
                },
                "chat_instance": "123",
                "data": data
            }
        }
        await self.send_mock_update(update_dict)


async def run_simulation():
    admin_id = 963819835
    channel_id = "@test_channel"

    print(f"Iniciando simulación de carga con {ITERACIONES} iteraciones...")
    print(f"Configuración: Productos/evento: {PRODUCTOS_POR_EVENTO}, Usuarios/evento: {USUARIOS_POR_EVENTO}\n")

    api_calls_stats = defaultdict(int)

    with patch('telegram.ext.ExtBot._do_post', new_callable=AsyncMock) as mock_do_post:
        def mock_do_post_side_effect(*args, **kwargs):
            endpoint = kwargs.get('endpoint') or args[0]
            api_calls_stats[endpoint] += 1
            if endpoint == 'getMe':
                return {"id": 1234, "is_bot": True, "first_name": "TestBot", "username": "test_bot"}
            return {"message_id": 9999, "date": int(time.time()), "chat": {"id": 1, "type": "private"}, "text": "mock"}

        mock_do_post.side_effect = mock_do_post_side_effect

        sim = BotSimulator(admin_id=admin_id, channel_id=channel_id)
        await sim.initialize()

        start_time = time.time()

        for iteration in range(1, ITERACIONES + 1):
            print(f"[{iteration}/{ITERACIONES}] Ejecutando flujo completo...")

            # 1. Start del bot
            await sim.cmd(admin_id, "/start")

            # 2. Creación de evento
            await sim.cmd(admin_id, "/evento")

            for p in range(PRODUCTOS_POR_EVENTO):
                await sim.photo(admin_id)
                await sim.msg(admin_id, f"Producto de prueba {p+1} - $10")

            # Finalizar productos
            await sim.msg(admin_id, "listo")
            # Título
            await sim.msg(admin_id, f"Evento de Prueba Simulación #{iteration}")

            # Fecha en el futuro
            futuro = datetime.now() + timedelta(minutes=1)
            fecha_str = futuro.strftime('%d/%m/%Y %H:%M')
            await sim.msg(admin_id, fecha_str)

            # Forzar ejecución de los jobs pendientes
            jobs = sim.app.job_queue.jobs()
            for job in jobs:
                if "send_event" in str(job.callback):
                    class MockContext:
                        def __init__(self, bot, bot_data, job):
                            self.bot = bot
                            self.bot_data = bot_data
                            self.job = job

                    mock_context = MockContext(sim.app.bot, sim.app.bot_data, job)
                    await job.callback(mock_context)
                    job.schedule_removal()

            # Encontrar el event_id recién creado
            events = sim.app.bot_data.get('events', {})
            event_id = list(events.keys())[-1]
            event_data = events[event_id]

            # 3. Interacciones de usuarios ("mio" callbacks)
            for i in range(USUARIOS_POR_EVENTO):
                user_id = 2000 + i
                await sim.cmd(user_id, "/start")

                if len(event_data['items']) > 0:
                    callback_data = f"mio|{event_id}|0"
                    await sim.callback_query(user_id, callback_data)

            # 4. Admin da Next para el siguiente producto
            if len(event_data['items']) > 1:
                callback_data = f"next|{event_id}|0"
                await sim.callback_query(admin_id, callback_data)

            # 5. Comandos de reporte
            await sim.cmd(2000, "/resumen")
            await sim.msg(2000, event_id)

            await sim.cmd(admin_id, "/consolidado")
            await sim.msg(admin_id, event_id)

            await sim.cmd(admin_id, "/historial")

            # Fin del evento
            await sim.cmd(admin_id, "/fin")
            await sim.msg(admin_id, event_id)

            # Limpiar
            await sim.cmd(admin_id, "/limpiar")
            await sim.msg(admin_id, event_id)

        await sim.stop()

        end_time = time.time()
        elapsed = end_time - start_time

        # ─── REPORTE ──────────────────────────────────────────────────────
        report = []
        report.append("═══════════════════════════════════════════════")
        report.append("       REPORTE DE COSTO Y TESTING DEL BOT      ")
        report.append("═══════════════════════════════════════════════")
        report.append(f"Fecha de ejecución      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"Iteraciones completadas : {ITERACIONES}")
        report.append(f"Productos por evento    : {PRODUCTOS_POR_EVENTO}")
        report.append(f"Usuarios por evento     : {USUARIOS_POR_EVENTO}")
        report.append(f"Tiempo de ejecución     : {elapsed:.2f} segundos")
        report.append("═══════════════════════════════════════════════")
        report.append("DESGLOSE DE PETICIONES HTTP A TELEGRAM:")
        report.append("")

        total_calls = sum(api_calls_stats.values())

        # Quitar getMe de los costos operativos (solo se llama 1 vez al iniciar)
        getme_calls = api_calls_stats.pop('getMe', 0)

        total_operative_calls = sum(api_calls_stats.values())

        if total_operative_calls == 0:
            report.append("  No se realizaron llamadas a la API.")
        else:
            for endpoint, count in sorted(api_calls_stats.items(), key=lambda x: x[1], reverse=True):
                bar = '█' * min(count, 40)
                report.append(f"  {endpoint:<25} : {count:>5} peticiones  {bar}")

        report.append("")
        report.append("───────────────────────────────────────────────")
        report.append(f"  getMe (inicialización)  : {getme_calls:>5} (no se cuenta como costo)")
        report.append(f"  TOTAL PETICIONES        : {total_operative_calls:>5}")
        report.append(f"  Peticiones por iteración: {total_operative_calls / max(ITERACIONES, 1):.1f}")
        report.append("───────────────────────────────────────────────")

        # Estimación de costos
        cost_per_req = 0.000002
        total_cost = total_operative_calls * cost_per_req
        report.append("")
        report.append("ESTIMACIÓN DE COSTOS:")
        report.append(f"  Costo por petición (ref): ${cost_per_req:.6f} USD")
        report.append(f"  Costo total estimado    : ${total_cost:.8f} USD")
        report.append("")
        report.append("NOTAS IMPORTANTES PARA HOSTING:")
        report.append("  • Railway cobra por tiempo de CPU/RAM, NO por peticiones HTTP.")
        report.append("  • Telegram permite ~30 peticiones/segundo por bot.")
        report.append(f"  • Con {PRODUCTOS_POR_EVENTO} productos y {USUARIOS_POR_EVENTO} usuarios,")
        report.append(f"    cada evento genera ~{total_operative_calls // max(ITERACIONES, 1)} peticiones.")
        report.append("  • Si el bot procesa un evento cada ~5 min, está lejos del límite.")
        report.append("═══════════════════════════════════════════════")

        report_text = "\n".join(report)
        print("\n" + report_text)

        with open("reporte_pruebas.txt", "w", encoding="utf-8") as f:
            f.write(report_text)

        print("\n✅ Reporte guardado en 'reporte_pruebas.txt'")


if __name__ == "__main__":
    asyncio.run(run_simulation())
