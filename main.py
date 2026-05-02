from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, InputMediaPhoto, InputMediaVideo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler, CallbackQueryHandler
from datetime import datetime
import time
import json
import os
import logging
import random
from dotenv import load_dotenv
from enum import IntEnum
from database import get_db

load_dotenv()


# ─── Estados para la conversación ─────────────────────────────────────────

class State(IntEnum):
    WAITING_FOR_MEDIA = 0
    WAITING_FOR_TITLE = 1
    WAITING_FOR_DATETIME = 2
    WAITING_FOR_EVENT_ID_RESUMEN = 3
    WAITING_FOR_EVENT_ID_FIN = 4
    WAITING_FOR_EVENT_ID_LIMPIAR = 5
    WAITING_FOR_EVENT_ID_CONSOLIDADO = 6


# ─── Helpers de configuración ─────────────────────────────────────────────

def get_config(context):
    """Obtiene la configuración de la empresa desde bot_data.
    Cada instancia de ShopperBot almacena su propia config aquí."""
    return context.bot_data.get('config', {})


# Mensaje de bienvenida (se genera dinámicamente)
def get_welcome_message(username, bot_name, is_authorized, store_name):
    base_msg = (
        f"*Hola {username} 👋*\n\n"
        f"Soy {bot_name}, tu asistente virtual para gestionar eventos de {store_name}.\n\n"
        "Comandos disponibles:\n"
    )
    if is_authorized:
        return base_msg + (
            "• /evento - Crear un nuevo evento\n"
            "• /resumen - Ver tus reservas\n"
            "• /fin - Finalizar un evento\n"
            "• /consolidado - Ver el consolidado de un evento\n"
            "• /historial - Consultar eventos finalizados\n"
            "• /limpiar - Limpiar los mensajes de un evento\n"
        )
    return base_msg + "• /resumen - Ver tus reservas en los eventos\n"


# ─── Persistencia ─────────────────────────────────────────────────────────

def load_data(bot_data, empresa_id, data_dir, data_file, legacy_file=None):
    """Carga datos persistentes desde MongoDB o disco.

    Args:
        bot_data: Diccionario de bot_data de la Application.
        empresa_id: ID de la empresa (ej. tienda_0001).
        data_dir: Directorio donde viven los datos de esta empresa (fallback).
        data_file: Ruta completa al archivo JSON de datos (fallback).
        legacy_file: Ruta opcional a un archivo legacy para migración (fallback).
    """
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)

    # Preservar config si ya fue seteada (para que bot_data.update no la borre)
    config_backup = bot_data.get('config')

    # Intentar cargar desde MongoDB primero
    db = get_db()
    if db is not None:
        try:
            doc = db.bot_data.find_one({"_id": empresa_id})
            if doc:
                doc.pop('_id', None)
                if 'events' not in bot_data:
                    bot_data['events'] = {}
                if 'events' in doc:
                    bot_data['events'].update(doc['events'])
                for k, v in doc.items():
                    if k != 'events':
                        bot_data[k] = v
                if config_backup:
                    bot_data['config'] = config_backup
                return
        except Exception as e:
            logging.error(f'Error cargando datos de MongoDB para {empresa_id}: {e}')
            # Cae al fallback JSON si MongoDB falla

    if legacy_file and os.path.exists(legacy_file) and os.path.getsize(legacy_file) > 0:
        try:
            with open(legacy_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                bot_data.update(data)
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f'Error cargando datos de {legacy_file}: {e}')

    if 'events' not in bot_data:
        bot_data['events'] = {}

    if os.path.exists(data_file) and os.path.getsize(data_file) > 0:
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                events = bot_data['events']
                if 'events' in data:
                    events.update(data['events'])
                bot_data.update(data)
                bot_data['events'] = events
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f'Error cargando datos de {data_file}: {e}')

    # Restaurar config
    if config_backup:
        bot_data['config'] = config_backup


def save_data(context):
    """Guarda datos persistentes a MongoDB o disco."""
    config = get_config(context)
    empresa_id = config.get('empresa_id')
    data_dir = config.get('data_dir', 'data')
    data_file = config.get('data_file', os.path.join(data_dir, 'bot_data.json'))
    
    # Filtrar 'config' para no guardarlo
    data_to_save = {k: v for k, v in context.bot_data.items() if k != 'config'}
    
    # Intentar guardar en MongoDB
    db = get_db()
    if db is not None and empresa_id:
        try:
            db.bot_data.replace_one({"_id": empresa_id}, data_to_save, upsert=True)
            return
        except Exception as e:
            logging.error(f'Error guardando datos en MongoDB para {empresa_id}: {e}')
            # Cae al fallback JSON si falla MongoDB

    # Fallback JSON
    try:
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        with open(data_file, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logging.error(f'Error guardando datos en {data_file}: {e}')


# ─── Utilidades ───────────────────────────────────────────────────────────

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

def get_consolidado_text(event: dict) -> str:
    user_reservations = {}
    for item in event.get('items', []):
        for click in item.get('clicks', []):
            uid, username = normalize_click(click)
            if uid not in user_reservations:
                user_reservations[uid] = {
                    'username': username,
                    'products': [],
                }
            user_reservations[uid]['products'].append(item['caption'])

    consolidado = f'*Consolidado del evento "{event.get("title", "")}"*\n\n'
    if user_reservations:
        for uid, data in user_reservations.items():
            user_label = format_user_identifier(uid, data['username'])
            consolidado += f'{user_label}: {", ".join(data["products"])}\n'
    else:
        consolidado += 'No hay reservas.\n'
    return consolidado


# ─── Handlers ─────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = get_config(context)
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.full_name or 'Usuario'
    bot_info = await context.bot.get_me()
    bot_name = bot_info.first_name
    is_authorized = user_id in config.get('authorized_users', [])
    welcome_message = get_welcome_message(username, bot_name, is_authorized, config.get('store_name', 'MIO'))
    await update.message.reply_text(welcome_message, parse_mode='Markdown')


async def event_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config = get_config(context)
    user_id = update.effective_user.id
    if user_id not in config.get('authorized_users', []):
        await update.message.reply_text('No tienes permisos para crear eventos. Solo vendedores/admins pueden usar este comando.')
        return ConversationHandler.END
    await update.message.reply_text(
        'Envíame una o varias fotos/videos para el producto. Cuando termines con las imágenes de este producto, escribe su descripción (y costo) para guardarlo. Para finalizar y armar el evento, envía "listo".'
    )
    context.user_data['items'] = []
    context.user_data['current_media'] = []
    return State.WAITING_FOR_MEDIA

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # 1. Recibir Media
    if update.message.photo or update.message.video:
        media_list = context.user_data.setdefault('current_media', [])
        if update.message.photo:
            media_list.append({'type': 'photo', 'file_id': update.message.photo[-1].file_id})
        elif update.message.video:
            media_list.append({'type': 'video', 'file_id': update.message.video.file_id})
            
        if len(media_list) == 1:
            await update.message.reply_text('📷 Recibido. Puedes seguir enviando más fotos/videos para AGREGARLOS al mismo producto, o envía la DESCRIPCIÓN en texto (ej. "Zapatos - $10") para guardarlo.')
        return State.WAITING_FOR_MEDIA

    # 2. Recibir Texto (Descripción o "listo")
    if update.message.text:
        text = update.message.text.strip()
        if text.lower() == 'listo':
            if context.user_data.get('current_media'):
                await update.message.reply_text('Tienes fotos/videos pendientes. Escribe la descripción para el último producto antes de terminar o envía más imágenes.')
                return State.WAITING_FOR_MEDIA
            if not context.user_data.get('items'):
                await update.message.reply_text('No has enviado productos. Envía al menos una foto/video seguida de su descripción.')
                return State.WAITING_FOR_MEDIA
                
            await update.message.reply_text('Ahora, envíame el título para todo evento.')
            return State.WAITING_FOR_TITLE
            
        else: # Text is a DESCRIPTION
            if not context.user_data.get('current_media'):
                await update.message.reply_text('Primero debes enviar fotos o videos antes de escribir una descripción.')
                return State.WAITING_FOR_MEDIA
                
            # Guardamos el producto con su álbum
            context.user_data['items'].append({
                'media': context.user_data['current_media'],
                'caption': text
            })
            items_count = len(context.user_data['items'])
            context.user_data['current_media'] = []
            await update.message.reply_text(f'✅ ¡Producto {items_count} guardado con éxito! Envía imágenes para el SIGUIENTE producto o escribe "listo" para terminar.')
            return State.WAITING_FOR_MEDIA

    await update.message.reply_text('Por favor, envía una foto, video o escribe "listo".')
    return State.WAITING_FOR_MEDIA

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['title'] = update.message.text
    await update.message.reply_text('Ahora, envíame la fecha y hora del evento en formato DD/MM/YYYY HH:MM (ej. 15/04/2026 14:30).')
    return State.WAITING_FOR_DATETIME

async def receive_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        event_datetime = datetime.strptime(text, '%d/%m/%Y %H:%M')
    except ValueError:
        await update.message.reply_text('Formato inválido. Usa DD/MM/YYYY HH:MM (ej. 15/04/2026 14:30).')
        return State.WAITING_FOR_DATETIME

    now = datetime.now()
    if event_datetime <= now:
        await update.message.reply_text('La fecha debe ser en el futuro. Intenta de nuevo.')
        return State.WAITING_FOR_DATETIME

    try:
        while True:
            event_id = str(random.randint(1000, 9999))
            if event_id not in context.bot_data.get('events', {}):
                break

        event_data = {
            'title': context.user_data['title'],
            'items': [item.copy() for item in context.user_data['items']],
            'event_id': event_id,
            'creator': update.effective_user.id,
            'event_datetime': text,
        }

        # Guardar evento inmediatamente en bot_data para persistencia
        events = context.bot_data.setdefault('events', {})
        items_for_db = [{'caption': item['caption'], 'media': item.get('media', []), 'clicks': [], 'message_ids': []} for item in context.user_data['items']]
        events[event_id] = {
            'title': context.user_data['title'],
            'creator': update.effective_user.id,
            'event_datetime': text,
            'items': items_for_db,
            'title_message_id': None,
            'programado': True,
        }
        save_data(context)

        # Programar el envío al canal
        delay = (event_datetime - datetime.now()).total_seconds()
        try:
            context.job_queue.run_once(send_event, delay, data=event_data)
        except Exception as e:
            logging.error(f"Error al programar el job del evento {event_id}: {e}")

        await update.message.reply_text(
            f'✅ *Evento creado exitosamente!*\n\n'
            f'📋 *Título:* {context.user_data["title"]}\n'
            f'📅 *Fecha:* {text}\n'
            f'🆔 *ID:* {event_id}\n'
            f'📦 *Productos:* {len(context.user_data["items"])}\n\n'
            f'El evento se enviará automáticamente al canal en la fecha indicada.',
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    except Exception as e:
        logging.error(f"Error inesperado al crear evento: {e}")
        await update.message.reply_text(f'❌ Error al crear el evento: {e}')
        return ConversationHandler.END


async def _send_product_to_channel(context: ContextTypes.DEFAULT_TYPE, item: dict, event_id: str, idx: int, total_items: int) -> bool:
    config = get_config(context)
    channel_id = config['channel_id']

    media = item.get('media', [])
    # Compatibilidad hacia atrás: si `photo` existe en lugar de `media` (eventos viejos)
    if not media and item.get('photo'):
        media = [{'type': 'photo', 'file_id': item['photo']}]
        
    caption = f"{item['caption']}\n({idx+1} de {total_items})"
    channel_buttons = [InlineKeyboardButton('Mio', callback_data=f'mio|{event_id}|{idx}')]
    message_ids = []
    
    try:
        if len(media) == 1:
            m = media[0]
            if m['type'] == 'photo':
                msg = await context.bot.send_photo(chat_id=channel_id, photo=m['file_id'], caption=caption, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([channel_buttons]))
            else:
                msg = await context.bot.send_video(chat_id=channel_id, video=m['file_id'], caption=caption, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([channel_buttons]))
            message_ids.append(msg.message_id)
        else:
            input_media = []
            for m in media:
                if m['type'] == 'photo':
                    input_media.append(InputMediaPhoto(m['file_id']))
                else:
                    input_media.append(InputMediaVideo(m['file_id']))
            # Enviar grupo
            msgs = await context.bot.send_media_group(chat_id=channel_id, media=input_media)
            for gm in msgs:
                message_ids.append(gm.message_id)
            # Enviar mensaje con botón e información
            btn_msg = await context.bot.send_message(chat_id=channel_id, text=f"📌 {caption}", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([channel_buttons]))
            message_ids.append(btn_msg.message_id)
            
        item['message_ids'] = message_ids
        save_data(context)
        return True
    except Exception as e:
        logging.error(f"Error enviando producto a canal: {e}")
        return False


async def send_event(context: ContextTypes.DEFAULT_TYPE) -> None:
    config = get_config(context)
    channel_id = config['channel_id']

    job = context.job
    data = job.data
    title = data['title']
    items = data['items']
    event_id = data['event_id']
    creator = data['creator']

    events = context.bot_data.setdefault('events', {})
    if event_id not in events:
        # Evento no existía (caso legacy o datos perdidos), crearlo
        events[event_id] = {
            'title': title,
            'creator': creator,
            'event_datetime': data.get('event_datetime', ''),
            'items': [{'caption': item['caption'], 'media': item.get('media', [{'type': 'photo', 'file_id': item.get('photo')}]), 'clicks': [], 'message_ids': []} for item in items],
            'title_message_id': None
        }
    # Marcar como ya enviado
    events[event_id].pop('programado', None)
    save_data(context)

    try:
        message = await context.bot.send_message(chat_id=channel_id, text=f'*Evento: {title}*\nID: {event_id}', parse_mode='Markdown')
        events[event_id]['title_message_id'] = message.message_id
        save_data(context)
    except Exception as e:
        logging.error(f"Error sending event title: {e}")
        return

    if len(items) > 0:
        idx = 0
        db_item = events[event_id]['items'][idx]
        success = await _send_product_to_channel(context, db_item, event_id, idx, len(items))
        if not success:
            return

        # Enviar control privado al creador
        if len(items) > 1:
            try:
                next_item = items[1]
                await context.bot.send_message(
                    chat_id=creator,
                    text=f'Control del evento "{title}"\nPróximo producto: *{next_item["caption"]}*\n\nPresiona Siguiente para enviar este producto al canal.',
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Siguiente', callback_data=f'next|{event_id}|{idx}')]]),
                )
            except Exception as e:
                logging.error(f"Error sending control to creator: {e}")

async def _process_next_product(query, event_id: str, current_idx: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = query.from_user.id
    events = context.bot_data.get('events', {})
    if event_id not in events:
        await query.answer(text='Evento no encontrado.', show_alert=True)
        return

    if user_id != events[event_id].get('creator'):
        await query.answer('Solo el creador puede avanzar al siguiente producto.', show_alert=True)
        return

    next_idx = current_idx + 1
    if next_idx >= len(events[event_id]['items']):
        await query.answer('No hay más productos.', show_alert=True)
        return

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    db_item = events[event_id]['items'][next_idx]
    
    success = await _send_product_to_channel(context, db_item, event_id, next_idx, len(events[event_id]['items']))
    if not success:
        await query.answer('Error al enviar el producto.', show_alert=True)
        return
        
    try:
        await query.answer('Producto siguiente enviado.', show_alert=False)
    except Exception:
        pass

    # Enviar control privado si hay más
    if next_idx + 1 < len(events[event_id]['items']):
        try:
            next_item = events[event_id]['items'][next_idx + 1]
            await context.bot.send_message(
                chat_id=events[event_id]['creator'],
                text=f'Control del evento "{events[event_id]["title"]}"\nPróximo producto: *{next_item["caption"]}*\n\nPresiona Siguiente para enviar este producto al canal.',
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Siguiente', callback_data=f'next|{event_id}|{next_idx}')]]),
            )
        except Exception as e:
            logging.error(f"Error sending control to creator: {e}")

async def _process_reserve_product(query, event_id: str, idx: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = get_config(context)
    channel_id = config['channel_id']

    user_id = query.from_user.id
    events = context.bot_data.get('events', {})
    
    if event_id not in events or idx >= len(events[event_id]['items']):
        await query.answer(text='Producto no encontrado.', show_alert=True)
        return

    item = events[event_id]['items'][idx]
    if item.get('clicks'):
        await query.answer(text='Este producto ya fue reservado por otro usuario.', show_alert=True)
        return

    # Reservar el producto
    username = query.from_user.username or query.from_user.full_name or ''
    item['clicks'].append({'id': user_id, 'username': username})
    save_data(context)

    # Editar el mensaje para remover el botón (siempre el último msg_id de la lista de producto)
    if 'message_id' in item and item['message_id']:
        item['message_ids'] = [item['message_id']]
        del item['message_id']
        
    if item.get('message_ids'):
        last_msg_id = item['message_ids'][-1]
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=channel_id,
                message_id=last_msg_id,
                reply_markup=None
            )
        except Exception as e:
            logging.warning(f"No se pudo limpiar los botones de evento {event_id} msj {last_msg_id}: {e}")

    summary = (
        f'*Resumen del producto*\n'
        f'{item["caption"]}\n\n'
        f'*Evento:* {events[event_id]["title"]}\n'
        f'✅ *Reservado para ti!*'
    )
    try:
        await context.bot.send_message(chat_id=user_id, text=summary, parse_mode='Markdown')
        await query.answer(text='Producto reservado. Te envié el resumen en privado.', show_alert=False)
    except Exception as e:
        logging.warning(f"No se pudo enviar mensaje final al usuario {user_id}: {e}")
        await query.answer(text='Producto reservado, pero no pude enviarte el mensaje privado. Inicia el bot con /start primero.', show_alert=True)

async def handle_mio_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()

    try:
        if query.data.startswith('next|'):
            _, event_id, idx_text = query.data.split('|', 2)
            await _process_next_product(query, event_id, int(idx_text), context)
            
        elif query.data.startswith('mio|'):
            _, event_id, idx_text = query.data.split('|', 2)
            await _process_reserve_product(query, event_id, int(idx_text), context)
    except ValueError as e:
        logging.error(f"Error al procesar los datos del botón: {query.data} -> {e}")
        await query.answer(text="Datos corruptos u operación inválida.", show_alert=True)

async def resumen_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Envíame el identificador único del evento para obtener tu resumen de productos.')
    return State.WAITING_FOR_EVENT_ID_RESUMEN

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
    config = get_config(context)
    user_id = update.effective_user.id
    if user_id not in config.get('authorized_users', []):
        await update.message.reply_text('No tienes permisos para finalizar eventos.')
        return ConversationHandler.END
    await update.message.reply_text('Envíame el identificador único del evento para finalizarlo y obtener el consolidado.')
    return State.WAITING_FOR_EVENT_ID_FIN

async def receive_event_id_fin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config = get_config(context)
    channel_id = config['channel_id']

    event_id = update.message.text.strip()
    events = context.bot_data.get('events', {})
    if event_id not in events:
        await update.message.reply_text('Evento no encontrado.')
        return ConversationHandler.END

    event = events[event_id]
    consolidado = get_consolidado_text(event)

    await update.message.reply_text(consolidado, parse_mode='Markdown')

    # Eliminar botones de productos no reservados
    for item in event['items']:
        if not item['clicks']:
            if 'message_id' in item and item['message_id']:
                item['message_ids'] = [item['message_id']]
                del item['message_id']
            if item.get('message_ids'):
                last_msg_id = item['message_ids'][-1]
                try:
                    await context.bot.edit_message_reply_markup(chat_id=channel_id, message_id=last_msg_id, reply_markup=None)
                except Exception as e:
                    logging.error(f"Error removing button for {item['caption']}: {e}")

    # Marcar evento como finalizado
    event['finalizado'] = True
    save_data(context)

    return ConversationHandler.END

async def consolidado_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config = get_config(context)
    user_id = update.effective_user.id
    if user_id not in config.get('authorized_users', []):
        await update.message.reply_text('No tienes permisos para ver consolidado de eventos.')
        return ConversationHandler.END
    await update.message.reply_text('Envíame el identificador único del evento para obtener el consolidado.')
    return State.WAITING_FOR_EVENT_ID_CONSOLIDADO

async def receive_event_id_consolidado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    event_id = update.message.text.strip()
    events = context.bot_data.get('events', {})
    if event_id not in events:
        await update.message.reply_text('Evento no encontrado.')
        return ConversationHandler.END

    event = events[event_id]
    consolidado = get_consolidado_text(event)

    await update.message.reply_text(consolidado, parse_mode='Markdown')

    return ConversationHandler.END

async def historial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = get_config(context)
    user_id = update.effective_user.id
    if user_id not in config.get('authorized_users', []):
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
        date_info = f" - *Fecha/Hora:* {event['event_datetime']}" if event.get('event_datetime') else ""
        historial_text += f'*ID:* {event_id} - *Título:* {event["title"]}{date_info}\n'

    await update.message.reply_text(historial_text, parse_mode='Markdown')

async def limpiar_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config = get_config(context)
    user_id = update.effective_user.id
    if user_id not in config.get('authorized_users', []):
        await update.message.reply_text('No tienes permisos para limpiar eventos.')
        return ConversationHandler.END
    await update.message.reply_text('Envíame el identificador único del evento para limpiar todos sus mensajes en el canal.')
    return State.WAITING_FOR_EVENT_ID_LIMPIAR

async def receive_event_id_limpiar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config = get_config(context)
    channel_id = config['channel_id']

    event_id = update.message.text.strip()
    events = context.bot_data.get('events', {})
    if event_id not in events:
        await update.message.reply_text('Evento no encontrado.')
        return ConversationHandler.END

    event = events[event_id]
    # Borrar título
    if event.get('title_message_id'):
        try:
            await context.bot.delete_message(chat_id=channel_id, message_id=event['title_message_id'])
        except Exception as e:
            logging.error(f"Error deleting title: {e}")

    # Borrar productos (todos sus mensajes en album)
    for item in event.get('items', []):
        if 'message_id' in item and item['message_id']:
            item['message_ids'] = [item['message_id']]
            del item['message_id']
            
        for msg_id in item.get('message_ids', []):
            try:
                await context.bot.delete_message(chat_id=channel_id, message_id=msg_id)
            except Exception as e:
                logging.error(f"Error deleting media part of product {item['caption']}: {e}")

    await update.message.reply_text('Mensajes del evento limpiados.')
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Conversación cancelada.')
    return ConversationHandler.END

async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.channel_post.text and update.channel_post.text.startswith('/start'):
        await context.bot.send_message(chat_id=update.channel_post.chat_id, text='Hello! I am your Telegram bot in this channel.')


# ─── Clase ShopperBot ─────────────────────────────────────────────────────

class ShopperBot:
    """Instancia de un bot de tienda con su propia configuración.
    
    Cada empresa/vendedor tiene su propio ShopperBot con:
    - Su propio token de bot (de BotFather)
    - Su propio canal de Telegram
    - Sus propios usuarios autorizados
    - Su propio directorio de datos
    """

    def __init__(self, bot_token, channel_id, authorized_users, store_name, data_dir):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.authorized_users = authorized_users
        self.store_name = store_name
        self.data_dir = data_dir
        self.data_file = os.path.join(data_dir, 'bot_data.json')
        self.application = None

    def build(self):
        """Construye y configura la Application de python-telegram-bot con todos los handlers."""
        self.application = ApplicationBuilder().token(self.bot_token).build()

        # Extraer empresa_id a partir del data_dir (ej. data/tienda_0001 -> tienda_0001)
        empresa_id = os.path.basename(self.data_dir)

        # Guardar config en bot_data para que los handlers la lean
        self.application.bot_data['config'] = {
            'empresa_id': empresa_id,
            'channel_id': self.channel_id,
            'authorized_users': self.authorized_users,
            'store_name': self.store_name,
            'data_dir': self.data_dir,
            'data_file': self.data_file,
        }

        # Cargar datos persistentes
        load_data(self.application.bot_data, empresa_id, self.data_dir, self.data_file)

        # Registrar handlers
        self._register_handlers()

        return self.application

    def _register_handlers(self):
        app = self.application

        app.add_handler(CommandHandler('start', start))

        # Unificar todos los flujos en un solo ConversationHandler para evitar
        # que comandos se bloqueen si el usuario deja una conversación a medias.
        main_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('evento', event_start),
                CommandHandler('resumen', resumen_start),
                CommandHandler('fin', fin_start),
                CommandHandler('limpiar', limpiar_start),
                CommandHandler('consolidado', consolidado_start),
            ],
            states={
                State.WAITING_FOR_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO | (filters.TEXT & ~filters.COMMAND), receive_media)],
                State.WAITING_FOR_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
                State.WAITING_FOR_DATETIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_datetime)],
                State.WAITING_FOR_EVENT_ID_RESUMEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_event_id_resumen)],
                State.WAITING_FOR_EVENT_ID_FIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_event_id_fin)],
                State.WAITING_FOR_EVENT_ID_LIMPIAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_event_id_limpiar)],
                State.WAITING_FOR_EVENT_ID_CONSOLIDADO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_event_id_consolidado)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel),
                # Capturar cualquier otro comando que no sea entry point para avisar o cancelar suavemente
                MessageHandler(filters.COMMAND, cancel)
            ],
            allow_reentry=True
        )
        app.add_handler(main_conv_handler)

        app.add_handler(CommandHandler('historial', historial))

        app.add_handler(CallbackQueryHandler(handle_mio_query, pattern=r'^(mio|next)\|'))
        app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.TEXT, handle_channel_message))


# ─── Ejecución standalone (compatibilidad hacia atrás) ────────────────────

if __name__ == '__main__':
    bot_token = os.getenv('BOT_TOKEN')
    if not bot_token:
        raise RuntimeError('BOT_TOKEN no está definido en .env')

    channel_id = os.getenv('CHANNEL_ID', '@000000000')
    authorized_users_str = os.getenv('AUTHORIZED_USERS', '')
    authorized_users = [int(uid.strip()) for uid in authorized_users_str.split(',') if uid.strip()]
    store_name = os.getenv('STORE_NAME', 'MIO')

    bot = ShopperBot(
        bot_token=bot_token,
        channel_id=channel_id,
        authorized_users=authorized_users,
        store_name=store_name,
        data_dir='data',
    )
    app = bot.build()
    app.run_polling(allowed_updates=['message', 'channel_post', 'callback_query'], drop_pending_updates=True)