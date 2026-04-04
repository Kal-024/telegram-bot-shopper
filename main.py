import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler, CallbackQueryHandler
from datetime import datetime
import time
import json
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

# ID del canal donde se enviarán los eventos (puede ser @username o ID numérico)
CHANNEL_ID = os.getenv('CHANNEL_ID', '@000000000')  # Reemplaza con tu canal o asegúrate de definirlo en .env

# Lista de IDs de usuarios autorizados para crear y finalizar eventos (vendedores/admins)
authorized_users_str = os.getenv('AUTHORIZED_USERS', '963819835')
AUTHORIZED_USERS = [int(uid.strip()) for uid in authorized_users_str.split(',') if uid.strip()]

# Nombre del bot y tienda
STORE_NAME = os.getenv('STORE_NAME', 'MIO')

# Mensaje de bienvenida (se genera dinámicamente)
def get_welcome_message(username, bot_name):
    return (
        f"*Hola {username} 👋*\n\n"
        f"Soy {bot_name}, tu asistente virtual para gestionar eventos de {STORE_NAME}.\n\n"
        "Comandos disponibles:\n"
        "• /evento - Crear un nuevo evento\n"
        "• /resumen - Ver tus reservas\n"
        "• /fin - Finalizar un evento\n"
        "• /consolidado - Ver el consolidado de un evento\n"
        "• /historial - Consultar eventos finalizados\n"
        "• /limpiar - Limpiar los mensajes de un evento\n"
    )

# Archivo para persistir datos de eventos y clics
DATA_FILE = 'bot_data.json'

# Token del bot (manténlo seguro, no lo compartas)
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise RuntimeError('BOT_TOKEN no está definido en .env')

# Estados para la conversación
WAITING_FOR_IMAGES, WAITING_FOR_IMAGE_DESC, WAITING_FOR_TITLE, WAITING_FOR_DATETIME = range(4)
WAITING_FOR_EVENT_ID_RESUMEN, WAITING_FOR_EVENT_ID_FIN, WAITING_FOR_EVENT_ID_LIMPIAR, WAITING_FOR_EVENT_ID_CONSOLIDADO = range(4, 8)

def load_data(context):
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            context.update(data)

def save_data(context):
    with open(DATA_FILE, 'w') as f:
        json.dump(dict(context.bot_data), f)


def normalize_click(click):
    if isinstance(click, dict):
        return click.get('id'), click.get('username', '')
    return click, ''


def format_user_identifier(user_id, username):
    if username:
        if username.startswith('@'):
            username_str = username
        elif ' ' in username:
            username_str = username
        else:
            username_str = f'@{username}'
        return f'@{user_id} - {username_str}'
    return f'@{user_id}'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username = update.effective_user.username or update.effective_user.full_name or 'Usuario'
    bot_info = await context.bot.get_me()
    bot_name = bot_info.first_name
    welcome_message = get_welcome_message(username, bot_name)
    await update.message.reply_text(welcome_message, parse_mode='Markdown')


async def event_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text('No tienes permisos para crear eventos. Solo vendedores/admins pueden usar este comando.')
        return ConversationHandler.END
    await update.message.reply_text(
        'Envíame las imágenes de los productos para el evento. Después de cada imagen, escribe una breve descripción con el costo. Cuando termines, escribe "listo".'
    )
    context.user_data['items'] = []
    context.user_data['pending_photo'] = None
    return WAITING_FOR_IMAGES

async def receive_images(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        context.user_data['pending_photo'] = file_id
        await update.message.reply_text(
            'Imagen recibida. Ahora envía una breve descripción para este producto (por ejemplo: "Camiseta negra - $20").'
        )
        return WAITING_FOR_IMAGE_DESC

    if update.message.text and update.message.text.lower() == 'listo':
        if context.user_data.get('pending_photo'):
            await update.message.reply_text('Debes enviar la descripción de la última imagen antes de terminar.')
            return WAITING_FOR_IMAGE_DESC
        if not context.user_data['items']:
            await update.message.reply_text('No has enviado productos. Envía al menos una imagen con su descripción.')
            return WAITING_FOR_IMAGES
        await update.message.reply_text('Ahora, envíame el título del evento.')
        return WAITING_FOR_TITLE

    await update.message.reply_text('Por favor, envía una imagen o escribe "listo".')
    return WAITING_FOR_IMAGES

async def receive_image_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    caption = update.message.text
    if not caption:
        await update.message.reply_text('Escribe una descripción breve para el producto.')
        return WAITING_FOR_IMAGE_DESC

    photo_id = context.user_data.get('pending_photo')
    if not photo_id:
        await update.message.reply_text('No hay imagen pendiente. Envía primero una imagen.')
        return WAITING_FOR_IMAGES

    context.user_data['items'].append({'photo': photo_id, 'caption': caption})
    context.user_data['pending_photo'] = None
    await update.message.reply_text('Producto guardado. Envía otra imagen o escribe "listo" para continuar.')
    return WAITING_FOR_IMAGES

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['title'] = update.message.text
    await update.message.reply_text('Ahora, envíame la fecha y hora del evento en formato DD/MM/YYYY HH:MM (ej. 15/04/2026 14:30).')
    return WAITING_FOR_DATETIME

async def receive_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        event_datetime = datetime.strptime(update.message.text, '%d/%m/%Y %H:%M')
        now = datetime.now()
        if event_datetime <= now:
            await update.message.reply_text('La fecha debe ser en el futuro. Intenta de nuevo.')
            return WAITING_FOR_DATETIME
        context.user_data['event_datetime'] = event_datetime
        event_id = str(int(time.time()))
        context.user_data['event_id'] = event_id
        context.user_data['creator'] = update.effective_user.id
        delay = (event_datetime - datetime.now()).total_seconds()
        context.job_queue.run_once(send_event, delay, data=context.user_data)
        await update.message.reply_text(f'Evento programado. Se enviará al canal en la fecha indicada. ID del evento: {event_id}')
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text('Formato inválido. Usa DD/MM/YYYY HH:MM.')
        return WAITING_FOR_DATETIME

async def send_event(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    data = job.data
    title = data['title']
    items = data['items']
    event_id = data['event_id']
    creator = data['creator']

    events = context.bot_data.setdefault('events', {})
    events[event_id] = {
        'title': title,
        'creator': creator,
        'items': [{'caption': item['caption'], 'photo': item['photo'], 'clicks': [], 'message_id': None} for item in items],
        'title_message_id': None
    }
    save_data(context)

    try:
        message = await context.bot.send_message(chat_id=CHANNEL_ID, text=f'*Evento: {title}*\nID: {event_id}', parse_mode='Markdown')
        events[event_id]['title_message_id'] = message.message_id
        save_data(context)
    except Exception as e:
        print(f"Error sending event title: {e}")
        return

    if items:
        idx = 0
        item = items[idx]
        caption = f"{item['caption']}\n({idx+1} de {len(items)})"
        try:
            message = await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=item['photo'],
                caption=caption,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Mio', callback_data=f'mio|{event_id}|{idx}')]]),
            )
            events[event_id]['items'][idx]['message_id'] = message.message_id
            save_data(context)
        except Exception as e:
            print(f"Error sending first product: {e}")
            return

        # Enviar control privado al creador
        if len(items) > 1:
            try:
                await context.bot.send_message(
                    chat_id=creator,
                    text=f'Control del evento "{title}": Presiona Siguiente para enviar el próximo producto.',
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Siguiente', callback_data=f'next|{event_id}|{idx}')]]),
                )
            except Exception as e:
                print(f"Error sending control to creator: {e}")
    events = context.bot_data.get('events', {})
    if event_id not in events or idx >= len(events[event_id]['items']):
        await query.answer(text='Producto no encontrado.', show_alert=True)
        return

async def handle_mio_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query  # type: CallbackQuery
    if not query:
        return

    await query.answer()
    if not query.data or not query.data.startswith(('mio|', 'next|')):
        return

    if query.data.startswith('next|'):
        _, event_id, idx_text = query.data.split('|', 2)
        current_idx = int(idx_text)
        user_id = query.from_user.id

        events = context.bot_data.get('events', {})
        if event_id not in events:
            await query.answer(text='Evento no encontrado.', show_alert=True)
            return

        if user_id != events[event_id]['creator']:
            await query.answer('Solo el creador puede avanzar al siguiente producto.', show_alert=True)
            return

        next_idx = current_idx + 1
        if next_idx >= len(events[event_id]['items']):
            await query.answer('No hay más productos.', show_alert=True)
            return

        item = events[event_id]['items'][next_idx]
        caption = f"{item['caption']}\n({next_idx+1} de {len(events[event_id]['items'])})"
        buttons = []
        if next_idx + 1 < len(events[event_id]['items']):
            buttons.append(InlineKeyboardButton('Siguiente', callback_data=f'next|{event_id}|{next_idx}'))
        buttons.append(InlineKeyboardButton('Mio', callback_data=f'mio|{event_id}|{next_idx}'))

        try:
            message = await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=item['photo'],
                caption=caption,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Mio', callback_data=f'mio|{event_id}|{next_idx}')]]),
            )
            events[event_id]['items'][next_idx]['message_id'] = message.message_id
            save_data(context)
            await query.answer('Producto siguiente enviado.', show_alert=False)

            # Enviar control privado si hay más
            if next_idx + 1 < len(events[event_id]['items']):
                try:
                    await context.bot.send_message(
                        chat_id=events[event_id]['creator'],
                        text=f'Control del evento "{events[event_id]["title"]}": Presiona Siguiente para enviar el próximo producto.',
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Siguiente', callback_data=f'next|{event_id}|{next_idx}')]]),
                    )
                except Exception as e:
                    print(f"Error sending control to creator: {e}")
        except Exception as e:
            print(f"Error sending next product: {e}")
            await query.answer('Error al enviar el producto.', show_alert=True)
        return

    # Handle 'mio|'
    _, event_id, idx_text = query.data.split('|', 2)
    idx = int(idx_text)
    user_id = query.from_user.id

    events = context.bot_data.get('events', {})
    if event_id not in events or idx >= len(events[event_id]['items']):
        await query.answer(text='Producto no encontrado.', show_alert=True)
        return

    item = events[event_id]['items'][idx]
    if item['clicks']:
        await query.answer(text='Este producto ya fue reservado por otro usuario.', show_alert=True)
        return

    # Reservar el producto
    username = query.from_user.username or query.from_user.full_name or ''
    item['clicks'].append({'id': user_id, 'username': username})
    save_data(context)

    # Editar el mensaje para remover el botón
    message_id = item.get('message_id')
    if message_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=CHANNEL_ID,
                message_id=message_id,
                reply_markup=None
            )
        except Exception:
            pass  # Ignorar errores si no se puede editar

    summary = (
        f'*Resumen del producto*\n'
        f'{item["caption"]}\n\n'
        f'*Evento:* {events[event_id]["title"]}\n'
        f'✅ *Reservado para ti!*'
    )
    try:
        await context.bot.send_message(chat_id=user_id, text=summary, parse_mode='Markdown')
        await query.answer(text='Producto reservado. Te envié el resumen en privado.', show_alert=False)
    except Exception:
        await query.answer(text='Producto reservado, pero no pude enviarte el mensaje privado. Inicia el bot con /start primero.', show_alert=True)

async def resumen_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Envíame el identificador único del evento para obtener tu resumen de productos.')
    return WAITING_FOR_EVENT_ID_RESUMEN

async def receive_event_id_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    event_id = update.message.text.strip()
    user_id = update.effective_user.id
    events = context.bot_data.get('events', {})
    if event_id not in events:
        await update.message.reply_text('Evento no encontrado. Verifica el identificador.')
        return ConversationHandler.END

    event = events[event_id]
    user_clicks = []
    for idx, item in enumerate(event['items']):
        if any(normalize_click(click)[0] == user_id for click in item['clicks']):
            user_clicks.append(f'{idx + 1}. {item["caption"]}')

    if not user_clicks:
        await update.message.reply_text('No has interactuado con productos en este evento.')
    else:
        summary = f'*Resumen de tus productos en el evento "{event["title"]}"*\n\n' + '\n'.join(user_clicks)
        await update.message.reply_text(summary, parse_mode='Markdown')
    return ConversationHandler.END

async def fin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text('No tienes permisos para finalizar eventos.')
        return ConversationHandler.END
    await update.message.reply_text('Envíame el identificador único del evento para finalizarlo y obtener el consolidado.')
    return WAITING_FOR_EVENT_ID_FIN

async def receive_event_id_fin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    event_id = update.message.text.strip()
    events = context.bot_data.get('events', {})
    if event_id not in events:
        await update.message.reply_text('Evento no encontrado.')
        return ConversationHandler.END

    event = events[event_id]
    user_reservations = {}
    for item in event['items']:
        for click in item['clicks']:
            uid, username = normalize_click(click)
            if uid not in user_reservations:
                user_reservations[uid] = {
                    'username': username,
                    'products': [],
                }
            user_reservations[uid]['products'].append(item['caption'])

    consolidado = f'*Consolidado del evento "{event["title"]}"*\n\n'
    if user_reservations:
        for uid, data in user_reservations.items():
            user_label = format_user_identifier(uid, data['username'])
            consolidado += f'{user_label}: {", ".join(data["products"])}\n'
    else:
        consolidado += 'No hay reservas.\n'

    await update.message.reply_text(consolidado, parse_mode='Markdown')

    # Eliminar botones de productos no reservados
    for item in event['items']:
        if not item['clicks'] and item['message_id']:
            try:
                await context.bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=item['message_id'], reply_markup=None)
            except Exception as e:
                print(f"Error removing button for {item['caption']}: {e}")

    # Marcar evento como finalizado
    event['finalizado'] = True
    save_data(context)

    return ConversationHandler.END

async def consolidado_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text('No tienes permisos para ver consolidado de eventos.')
        return ConversationHandler.END
    await update.message.reply_text('Envíame el identificador único del evento para obtener el consolidado.')
    return WAITING_FOR_EVENT_ID_CONSOLIDADO

async def receive_event_id_consolidado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    event_id = update.message.text.strip()
    events = context.bot_data.get('events', {})
    if event_id not in events:
        await update.message.reply_text('Evento no encontrado.')
        return ConversationHandler.END

    event = events[event_id]
    user_reservations = {}
    for item in event['items']:
        for click in item['clicks']:
            uid, username = normalize_click(click)
            if uid not in user_reservations:
                user_reservations[uid] = {
                    'username': username,
                    'products': [],
                }
            user_reservations[uid]['products'].append(item['caption'])

    consolidado = f'*Consolidado del evento "{event["title"]}"*\n\n'
    if user_reservations:
        for uid, data in user_reservations.items():
            user_label = format_user_identifier(uid, data['username'])
            consolidado += f'{user_label}: {", ".join(data["products"])}\n'
    else:
        consolidado += 'No hay reservas.\n'

    await update.message.reply_text(consolidado, parse_mode='Markdown')

    return ConversationHandler.END

async def historial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text('No tienes permisos para ver el historial de eventos.')
        return

    events = context.bot_data.get('events', {})
    finalizados = [event_id for event_id, event in events.items() if event.get('finalizado', False)]

    if not finalizados:
        await update.message.reply_text('No hay eventos finalizados.')
        return

    historial_text = '*Historial de eventos finalizados:*\n\n'
    for event_id in finalizados:
        event = events[event_id]
        historial_text += f'ID: {event_id} - Título: {event["title"]}\n'

    await update.message.reply_text(historial_text, parse_mode='Markdown')

async def limpiar_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text('No tienes permisos para limpiar eventos.')
        return ConversationHandler.END
    await update.message.reply_text('Envíame el identificador único del evento para limpiar todos sus mensajes en el canal.')
    return WAITING_FOR_EVENT_ID_LIMPIAR

async def receive_event_id_limpiar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    event_id = update.message.text.strip()
    events = context.bot_data.get('events', {})
    if event_id not in events:
        await update.message.reply_text('Evento no encontrado.')
        return ConversationHandler.END

    event = events[event_id]
    # Borrar título
    if event.get('title_message_id'):
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=event['title_message_id'])
        except Exception as e:
            print(f"Error deleting title: {e}")

    # Borrar productos
    for item in event['items']:
        if item['message_id']:
            try:
                await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=item['message_id'])
            except Exception as e:
                print(f"Error deleting product {item['caption']}: {e}")

    await update.message.reply_text('Mensajes del evento limpiados.')
    return ConversationHandler.END
    # save_data(context)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Conversación cancelada.')
    return ConversationHandler.END

async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.channel_post.text and update.channel_post.text.startswith('/start'):
        await context.bot.send_message(chat_id=update.channel_post.chat_id, text='Hello! I am your Telegram bot in this channel.')



if __name__ == '__main__':
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Cargar datos persistentes
    load_data(application.bot_data)
    
    application.add_handler(CommandHandler('start', start))
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('evento', event_start)],
        states={
            WAITING_FOR_IMAGES: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), receive_images)],
            WAITING_FOR_IMAGE_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_image_desc)],
            WAITING_FOR_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
            WAITING_FOR_DATETIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_datetime)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(conv_handler)
    
    resumen_handler = ConversationHandler(
        entry_points=[CommandHandler('resumen', resumen_start)],
        states={
            WAITING_FOR_EVENT_ID_RESUMEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_event_id_resumen)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(resumen_handler)
    
    fin_handler = ConversationHandler(
        entry_points=[CommandHandler('fin', fin_start)],
        states={
            WAITING_FOR_EVENT_ID_FIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_event_id_fin)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(fin_handler)
    
    limpiar_handler = ConversationHandler(
        entry_points=[CommandHandler('limpiar', limpiar_start)],
        states={
            WAITING_FOR_EVENT_ID_LIMPIAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_event_id_limpiar)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(limpiar_handler)
    
    consolidado_handler = ConversationHandler(
        entry_points=[CommandHandler('consolidado', consolidado_start)],
        states={
            WAITING_FOR_EVENT_ID_CONSOLIDADO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_event_id_consolidado)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(consolidado_handler)
    
    application.add_handler(CommandHandler('historial', historial))
    
    application.add_handler(CallbackQueryHandler(handle_mio_query, pattern=r'^(mio|next)\|'))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.TEXT, handle_channel_message))
    application.run_polling(allowed_updates=['message', 'channel_post', 'callback_query'], drop_pending_updates=True)