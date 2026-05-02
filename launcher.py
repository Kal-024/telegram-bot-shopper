"""
Launcher - Orquestador Multi-Tenant.

Punto de entrada principal del sistema. Se encarga de:
1. Migrar datos del formato antiguo (single-tenant) al nuevo registro
2. Cargar el registro de empresas (registry.json)
3. Levantar un bot por cada empresa activa
4. Levantar el Bot Maestro (admin) para gestión
5. Correr todo en paralelo con asyncio
"""

import asyncio
import signal
import os
import json
import shutil
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from main import ShopperBot
from admin_bot import build_admin_bot, load_registry, save_registry, REGISTRY_FILE


# ─── Migración de datos legacy ────────────────────────────────────────────

def migrate_legacy_data():
    """Migra datos del formato antiguo (un solo bot, .env simple) al nuevo registro multi-tenant.

    Detecta si:
    - Existe data/bot_data.json (datos del bot antiguo)
    - NO existe data/registry.json (aún no se ha migrado)
    - Existe BOT_TOKEN en .env (token del bot antiguo)

    Si se cumplen las condiciones, crea la primera empresa en el registro
    y mueve los datos a data/tienda_0001/.
    """
    legacy_data = os.path.join('data', 'bot_data.json')
    legacy_root_data = '.bot_data.json'

    # Si ya hay registro (en Mongo o en JSON local), no migrar
    registry = load_registry()
    if registry.get('empresas'):
        return

    bot_token = os.getenv('BOT_TOKEN')
    if not bot_token:
        return  # No hay bot legacy que migrar

    channel_id = os.getenv('CHANNEL_ID', '@000000000')
    authorized_users_str = os.getenv('AUTHORIZED_USERS', '')
    authorized_users = [int(uid.strip()) for uid in authorized_users_str.split(',') if uid.strip()]
    store_name = os.getenv('STORE_NAME', 'MIO')

    empresa_id = 'tienda_0001'
    empresa_dir = os.path.join('data', empresa_id)
    os.makedirs(empresa_dir, exist_ok=True)

    # Mover datos existentes a la carpeta de la empresa
    if os.path.exists(legacy_data):
        dest = os.path.join(empresa_dir, 'bot_data.json')
        shutil.copy2(legacy_data, dest)
        logging.info(f'Datos migrados: {legacy_data} → {dest}')

    # También copiar el archivo legacy de la raíz si existe
    if os.path.exists(legacy_root_data):
        dest = os.path.join(empresa_dir, '.bot_data_legacy.json')
        shutil.copy2(legacy_root_data, dest)
        logging.info(f'Datos legacy migrados: {legacy_root_data} → {dest}')

    # Crear registro con la tienda actual
    registry = {
        'empresas': {
            empresa_id: {
                'bot_token': bot_token,
                'channel_id': channel_id,
                'store_name': store_name,
                'authorized_users': authorized_users,
                'activo': True,
                'fecha_registro': datetime.now().strftime('%Y-%m-%d %H:%M'),
            }
        }
    }
    save_registry(registry)
    logging.info(f'✅ Migración completada. Tienda "{store_name}" registrada como {empresa_id}.')


# ─── Arranque principal ──────────────────────────────────────────────────

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Paso 1: Migrar datos si es necesario
    migrate_legacy_data()

    # Paso 2: Cargar registro
    registry = load_registry()
    empresas = registry.get('empresas', {})

    apps = []

    # Paso 3: Levantar bots de tiendas
    for empresa_id, config in empresas.items():
        if not config.get('activo', False):
            logging.info(f'⏸  Bot {empresa_id} ({config["store_name"]}) está desactivado, omitiendo.')
            continue

        data_dir = os.path.join('data', empresa_id)
        bot = ShopperBot(
            bot_token=config['bot_token'],
            channel_id=config['channel_id'],
            authorized_users=config['authorized_users'],
            store_name=config['store_name'],
            data_dir=data_dir,
        )
        app = bot.build()
        apps.append((empresa_id, config['store_name'], app))
        logging.info(f'🏪 Bot configurado: {empresa_id} - {config["store_name"]}')

    # Paso 4: Levantar bot admin
    admin_token = os.getenv('ADMIN_BOT_TOKEN')
    if admin_token:
        admin_app = build_admin_bot(admin_token)
        apps.append(('admin', 'Bot Maestro', admin_app))
        logging.info('🛠  Bot Maestro (admin) configurado.')
    else:
        logging.warning('⚠️  ADMIN_BOT_TOKEN no definido. El Bot Maestro no estará disponible.')

    if not apps:
        logging.error('❌ No hay bots configurados. Define ADMIN_BOT_TOKEN o agrega empresas al registro.')
        return

    # Paso 5: Inicializar y arrancar todos los bots
    for empresa_id, nombre, app in apps:
        await app.initialize()
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=['message', 'channel_post', 'callback_query']
        )
        await app.start()
        logging.info(f'✅ Bot activo: {nombre} ({empresa_id})')

    logging.info(f'\n🚀 {len(apps)} bot(s) corriendo. Presiona Ctrl+C para detener.\n')

    # Paso 6: Esperar señal de terminación
    stop_event = asyncio.Event()

    try:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
    except NotImplementedError:
        # Windows no soporta add_signal_handler, se maneja con KeyboardInterrupt
        pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass

    # Paso 7: Apagado limpio
    logging.info('Deteniendo bots...')
    for empresa_id, nombre, app in reversed(apps):
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            logging.info(f'Bot detenido: {nombre} ({empresa_id})')
        except Exception as e:
            logging.error(f'Error deteniendo {nombre}: {e}')

    logging.info('✅ Todos los bots detenidos.')


if __name__ == '__main__':
    asyncio.run(main())
