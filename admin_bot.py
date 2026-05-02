"""
Bot Maestro (Admin) - Gestión de empresas/tiendas.

Este bot es exclusivo del super-administrador y permite registrar,
listar, detener, reanudar y eliminar empresas del sistema multi-tenant.
Los datos de empresas se almacenan en data/registry.json.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler, CallbackQueryHandler
)
from enum import IntEnum
from datetime import datetime
import json
import os
import re
import logging


# ─── Configuración ────────────────────────────────────────────────────────

REGISTRY_DIR = 'data'
REGISTRY_FILE = os.path.join(REGISTRY_DIR, 'registry.json')


# ─── Estados de conversación para /registrar ──────────────────────────────

class AdminState(IntEnum):
    WAITING_TOKEN = 0
    WAITING_CHANNEL = 1
    WAITING_STORE_NAME = 2
    WAITING_USERS = 3
    WAITING_CONFIRM = 4


# ─── Funciones de registro ────────────────────────────────────────────────

def load_registry():
    """Carga el registro de empresas desde disco."""
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f'Error cargando registro: {e}')
    return {'empresas': {}}


def save_registry(registry):
    """Guarda el registro de empresas a disco."""
    os.makedirs(REGISTRY_DIR, exist_ok=True)
    try:
        with open(REGISTRY_FILE, 'w', encoding='utf-8') as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logging.error(f'Error guardando registro: {e}')


def generate_empresa_id(registry):
    """Genera un ID único incremental para la nueva empresa."""
    existing = registry.get('empresas', {})
    counter = 1
    while f'tienda_{counter:04d}' in existing:
        counter += 1
    return f'tienda_{counter:04d}'


def get_super_admin_id():
    """Obtiene el ID del super-administrador desde variables de entorno."""
    admin_id = os.getenv('SUPER_ADMIN_ID', '')
    if admin_id:
        return int(admin_id)
    return None


def is_super_admin(user_id):
    """Verifica si un usuario es el super-administrador."""
    super_admin = get_super_admin_id()
    return super_admin is not None and user_id == super_admin


# ─── Handlers del Bot Maestro ─────────────────────────────────────────────

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_super_admin(user_id):
        await update.message.reply_text('⛔ No tienes acceso a este bot.')
        return

    await update.message.reply_text(
        '<b>🛠 Panel de Administración - Bot Maestro</b>\n\n'
        'Comandos disponibles:\n\n'
        '• /registrar - Registrar una nueva empresa/tienda\n'
        '• /listar - Ver todas las empresas registradas\n'
        '• /estado - Ver estado de los bots activos\n'
        '• /detener &lt;id&gt; - Desactivar el bot de una empresa\n'
        '• /reanudar &lt;id&gt; - Reactivar el bot de una empresa\n'
        '• /eliminar &lt;id&gt; - Eliminar una empresa del sistema\n',
        parse_mode='HTML'
    )


# ─── Flujo de /registrar ─────────────────────────────────────────────────

async def registrar_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not is_super_admin(user_id):
        await update.message.reply_text('⛔ No tienes acceso a este comando.')
        return ConversationHandler.END

    await update.message.reply_text(
        '<b>📝 Registro de nueva empresa</b>\n\n'
        'Paso 1/4: Envíame el <b>token del bot</b> que creaste en @BotFather para esta tienda.\n\n'
        '<i>Ejemplo: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz</i>',
        parse_mode='HTML'
    )
    # Limpiar datos temporales
    context.user_data['new_empresa'] = {}
    return AdminState.WAITING_TOKEN


async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    token = update.message.text.strip()

    # Validar formato del token
    if not re.match(r'^\d+:[A-Za-z0-9_-]+$', token):
        await update.message.reply_text(
            '❌ Formato de token inválido.\n'
            'El token debe tener el formato: <code>número:letras</code>\n'
            'Intenta de nuevo o envía /cancel para cancelar.',
            parse_mode='HTML'
        )
        return AdminState.WAITING_TOKEN

    # Verificar que no esté ya registrado
    registry = load_registry()
    for emp_id, emp_data in registry.get('empresas', {}).items():
        if emp_data.get('bot_token') == token:
            import html
            await update.message.reply_text(
                f'⚠️ Este token ya está registrado como <b>{html.escape(emp_data["store_name"])}</b> (ID: <code>{emp_id}</code>).\n'
                'Envía otro token o /cancel para cancelar.',
                parse_mode='HTML'
            )
            return AdminState.WAITING_TOKEN

    context.user_data['new_empresa']['bot_token'] = token
    await update.message.reply_text(
        'Paso 2/4: Envíame el <b>ID del canal</b> donde este bot publicará eventos.\n\n'
        '<i>Puede ser @nombreDelCanal o un ID numérico negativo (ej: -1001234567890)</i>',
        parse_mode='HTML'
    )
    return AdminState.WAITING_CHANNEL


async def receive_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    channel = update.message.text.strip()

    # Validar formato básico
    if not (channel.startswith('@') or channel.startswith('-')):
        await update.message.reply_text(
            '❌ Formato inválido. Debe comenzar con @ o ser un ID numérico negativo.\n'
            '<i>Ejemplo: @micanal o -1001234567890</i>',
            parse_mode='HTML'
        )
        return AdminState.WAITING_CHANNEL

    context.user_data['new_empresa']['channel_id'] = channel
    await update.message.reply_text(
        'Paso 3/4: Envíame el <b>nombre de la tienda</b>.\n\n'
        '<i>Ejemplo: Fashion Store, Zapatos HN, Mi Tienda</i>',
        parse_mode='HTML'
    )
    return AdminState.WAITING_STORE_NAME


async def receive_store_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    store_name = update.message.text.strip()

    if len(store_name) < 2:
        await update.message.reply_text('❌ El nombre debe tener al menos 2 caracteres.')
        return AdminState.WAITING_STORE_NAME

    context.user_data['new_empresa']['store_name'] = store_name
    await update.message.reply_text(
        'Paso 4/4: Envíame los <b>IDs de los usuarios autorizados</b> (vendedores/admins) para esta tienda.\n\n'
        '<i>Separados por coma. Ejemplo: 123456789, 987654321</i>\n'
        '<i>Puedes usar @userinfobot en Telegram para obtener IDs.</i>',
        parse_mode='HTML'
    )
    return AdminState.WAITING_USERS


async def receive_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    try:
        user_ids = [int(uid.strip()) for uid in text.split(',') if uid.strip()]
        if not user_ids:
            raise ValueError("Lista vacía")
    except ValueError:
        await update.message.reply_text(
            '❌ Formato inválido. Envía IDs numéricos separados por coma.\n'
            '<i>Ejemplo: 123456789, 987654321</i>',
            parse_mode='HTML'
        )
        return AdminState.WAITING_USERS

    context.user_data['new_empresa']['authorized_users'] = user_ids

    # Mostrar resumen para confirmación
    emp = context.user_data['new_empresa']
    token_masked = emp['bot_token'][:8] + '...' + emp['bot_token'][-6:]

    import html
    summary = (
        '<b>📋 Resumen de la nueva empresa:</b>\n\n'
        f'🏪 <b>Tienda:</b> {html.escape(emp["store_name"])}\n'
        f'🤖 <b>Token:</b> <code>{token_masked}</code>\n'
        f'📢 <b>Canal:</b> {html.escape(emp["channel_id"])}\n'
        f'👥 <b>Usuarios autorizados:</b> {", ".join(str(u) for u in emp["authorized_users"])}\n\n'
        '¿Confirmar registro? Escribe <b>sí</b> o <b>no</b>.'
    )
    await update.message.reply_text(summary, parse_mode='HTML')
    return AdminState.WAITING_CONFIRM


async def receive_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()

    if text in ('sí', 'si', 's', 'yes', 'y'):
        emp = context.user_data['new_empresa']
        registry = load_registry()
        empresa_id = generate_empresa_id(registry)

        # Crear directorio de datos para la empresa
        empresa_dir = os.path.join('data', empresa_id)
        os.makedirs(empresa_dir, exist_ok=True)

        registry['empresas'][empresa_id] = {
            'bot_token': emp['bot_token'],
            'channel_id': emp['channel_id'],
            'store_name': emp['store_name'],
            'authorized_users': emp['authorized_users'],
            'activo': True,
            'fecha_registro': datetime.now().strftime('%Y-%m-%d %H:%M'),
        }
        save_registry(registry)

        import html
        await update.message.reply_text(
            f'✅ <b>Empresa registrada exitosamente!</b>\n\n'
            f'🔑 <b>ID:</b> <code>{empresa_id}</code>\n'
            f'🏪 <b>Tienda:</b> {html.escape(emp["store_name"])}\n\n'
            f'⚠️ <b>Reinicia el servidor</b> (deployment) para que el nuevo bot comience a funcionar.',
            parse_mode='HTML'
        )
        context.user_data.pop('new_empresa', None)
        return ConversationHandler.END

    elif text in ('no', 'n'):
        await update.message.reply_text('❌ Registro cancelado.')
        context.user_data.pop('new_empresa', None)
        return ConversationHandler.END

    else:
        await update.message.reply_text('Escribe <b>sí</b> o <b>no</b>.', parse_mode='HTML')
        return AdminState.WAITING_CONFIRM


# ─── Comandos de gestión ──────────────────────────────────────────────────

async def listar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text('⛔ No tienes acceso.')
        return

    registry = load_registry()
    empresas = registry.get('empresas', {})

    if not empresas:
        await update.message.reply_text('No hay empresas registradas. Usa /registrar para agregar una.')
        return

    import html
    text = '<b>📋 Empresas registradas:</b>\n\n'
    for emp_id, data in empresas.items():
        estado = '🟢 Activo' if data.get('activo', False) else '🔴 Detenido'
        text += (
            f'<b>{emp_id}</b> — {html.escape(data["store_name"])}\n'
            f'   Canal: {html.escape(data["channel_id"])}\n'
            f'   Estado: {estado}\n'
            f'   Usuarios: {", ".join(str(u) for u in data.get("authorized_users", []))}\n'
            f'   Registrado: {data.get("fecha_registro", "N/A")}\n\n'
        )
    await update.message.reply_text(text, parse_mode='HTML')


async def estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text('⛔ No tienes acceso.')
        return

    registry = load_registry()
    empresas = registry.get('empresas', {})

    if not empresas:
        await update.message.reply_text('No hay empresas registradas.')
        return

    activas = sum(1 for e in empresas.values() if e.get('activo', False))
    detenidas = len(empresas) - activas

    import html
    text = (
        '<b>📊 Estado del sistema:</b>\n\n'
        f'Total de empresas: {len(empresas)}\n'
        f'🟢 Activas: {activas}\n'
        f'🔴 Detenidas: {detenidas}\n\n'
    )

    for emp_id, data in empresas.items():
        icono = '🟢' if data.get('activo', False) else '🔴'
        text += f'{icono} <code>{emp_id}</code> — {html.escape(data["store_name"])}\n'

    await update.message.reply_text(text, parse_mode='HTML')


async def detener(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text('⛔ No tienes acceso.')
        return

    args = context.args
    if not args:
        await update.message.reply_text('Uso: /detener &lt;empresa_id&gt;\n<i>Ejemplo: /detener tienda_0001</i>', parse_mode='HTML')
        return

    empresa_id = args[0]
    registry = load_registry()

    if empresa_id not in registry.get('empresas', {}):
        await update.message.reply_text(f'❌ Empresa <code>{empresa_id}</code> no encontrada.', parse_mode='HTML')
        return

    registry['empresas'][empresa_id]['activo'] = False
    save_registry(registry)

    import html
    await update.message.reply_text(
        f'🔴 Empresa <code>{empresa_id}</code> ({html.escape(registry["empresas"][empresa_id]["store_name"])}) marcada como <b>detenida</b>.\n'
        f'Reinicia el servidor para aplicar los cambios.',
        parse_mode='HTML'
    )


async def reanudar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text('⛔ No tienes acceso.')
        return

    args = context.args
    if not args:
        await update.message.reply_text('Uso: /reanudar &lt;empresa_id&gt;\n<i>Ejemplo: /reanudar tienda_0001</i>', parse_mode='HTML')
        return

    empresa_id = args[0]
    registry = load_registry()

    if empresa_id not in registry.get('empresas', {}):
        await update.message.reply_text(f'❌ Empresa <code>{empresa_id}</code> no encontrada.', parse_mode='HTML')
        return

    registry['empresas'][empresa_id]['activo'] = True
    save_registry(registry)

    import html
    await update.message.reply_text(
        f'🟢 Empresa <code>{empresa_id}</code> ({html.escape(registry["empresas"][empresa_id]["store_name"])}) marcada como <b>activa</b>.\n'
        f'Reinicia el servidor para aplicar los cambios.',
        parse_mode='HTML'
    )


async def eliminar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text('⛔ No tienes acceso.')
        return

    args = context.args
    if not args:
        await update.message.reply_text('Uso: /eliminar &lt;empresa_id&gt;\n<i>Ejemplo: /eliminar tienda_0001</i>', parse_mode='HTML')
        return

    empresa_id = args[0]
    registry = load_registry()

    if empresa_id not in registry.get('empresas', {}):
        await update.message.reply_text(f'❌ Empresa <code>{empresa_id}</code> no encontrada.', parse_mode='HTML')
        return

    empresa_name = registry['empresas'][empresa_id]['store_name']
    del registry['empresas'][empresa_id]
    save_registry(registry)

    import html
    await update.message.reply_text(
        f'🗑 Empresa <code>{empresa_id}</code> (<b>{html.escape(empresa_name)}</b>) eliminada del registro.\n'
        f'⚠️ Los datos de la empresa en <code>data/{empresa_id}/</code> NO se han borrado por seguridad.\n'
        f'Reinicia el servidor para aplicar los cambios.',
        parse_mode='HTML'
    )


async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop('new_empresa', None)
    await update.message.reply_text('❌ Operación cancelada.')
    return ConversationHandler.END


# ─── Constructor del Bot Admin ────────────────────────────────────────────

def build_admin_bot(admin_token):
    """Construye y retorna la Application del bot administrador."""
    app = ApplicationBuilder().token(admin_token).build()

    app.add_handler(CommandHandler('start', admin_start))

    registrar_handler = ConversationHandler(
        entry_points=[CommandHandler('registrar', registrar_start)],
        states={
            AdminState.WAITING_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
            AdminState.WAITING_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel)],
            AdminState.WAITING_STORE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_store_name)],
            AdminState.WAITING_USERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_users)],
            AdminState.WAITING_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_confirm)],
        },
        fallbacks=[CommandHandler('cancel', admin_cancel)],
    )
    app.add_handler(registrar_handler)

    app.add_handler(CommandHandler('listar', listar))
    app.add_handler(CommandHandler('estado', estado))
    app.add_handler(CommandHandler('detener', detener))
    app.add_handler(CommandHandler('reanudar', reanudar))
    app.add_handler(CommandHandler('eliminar', eliminar))

    return app
