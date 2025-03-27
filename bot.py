import asyncio
import logging
import re
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneNumberInvalidError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.error import TimedOut

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Telegram API credentials
API_ID = 27011465
API_HASH = '9a32b60f759c605650699cc2591abf17'
ADMIN_ID = 5190379049
BOT_TOKEN = '8050994903:AAE4Vjqgpzxh7SfQQgJrgVWCYKMnZLpwVg4'

# Store configurations for multiple target channels
CONFIGURATIONS = {}  # {target_channel_id: {'active': True, 'sources': [], 'filters': {}, 'word_replace': {}, 'link_replace': {}}}

# Initialize Telegram client
client = TelegramClient('session_name', API_ID, API_HASH)

# Initialize bot application
application = Application.builder().token(BOT_TOKEN).build()

# Track login state
IS_LOGGED_IN = False

# Function to check if user is admin
def is_admin(user_id):
    return user_id == ADMIN_ID

# Main menu with dynamic buttons for configured channels
def get_main_menu():
    keyboard = []
    for target_id in CONFIGURATIONS:
        try:
            entity = asyncio.run(client.get_entity(target_id))
            channel_name = entity.title
        except Exception as e:
            logger.error(f"Error fetching channel name for {target_id}: {str(e)}")
            channel_name = f"Target {target_id}"
        status = "ON" if CONFIGURATIONS[target_id]['active'] else "OFF"
        keyboard.append([InlineKeyboardButton(f"Configured: {channel_name} ({status})", callback_data=f'edit_{target_id}')])
    keyboard.append([InlineKeyboardButton("Add Channel Configuration", callback_data='add_config')])
    return InlineKeyboardMarkup(keyboard)

# Configuration menu with on/off toggle
def get_config_menu(target_id=None):
    config = CONFIGURATIONS.get(target_id, {'active': True})
    status = "ON" if config['active'] else "OFF"
    keyboard = [
        [InlineKeyboardButton(f"Toggle Config: {status}", callback_data=f'toggle_config_{target_id}')],
        [InlineKeyboardButton("Add Source Channel", callback_data='set_source')],
        [InlineKeyboardButton("Set Target Channel", callback_data='set_target')],
        [InlineKeyboardButton("Set Message Filters", callback_data='set_filters')],
        [InlineKeyboardButton("Word Replace", callback_data='word_replace')],
        [InlineKeyboardButton("Check Status", callback_data='check_status')],
        [InlineKeyboardButton("Return to Main Menu", callback_data='return_main')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Filter type selection menu
def get_filter_type_menu():
    keyboard = [
        [InlineKeyboardButton("Text Only", callback_data='filter_text')],
        [InlineKeyboardButton("Files Only", callback_data='filter_file')],
        [InlineKeyboardButton("Both", callback_data='filter_both')],
        [InlineKeyboardButton("Clear Filters", callback_data='filter_clear')],
        [InlineKeyboardButton("Return", callback_data='return_config')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Word replace menu
def get_word_replace_menu(target_id=None):
    config = CONFIGURATIONS.get(target_id, {'word_replace': {'active': False, 'pairs': {}}, 'link_replace': {'active': False, 'replacement': '[Link Removed]'}})
    word_status = "ON" if config['word_replace']['active'] else "OFF"
    link_status = "ON" if config['link_replace']['active'] else "OFF"
    keyboard = [
        [InlineKeyboardButton(f"Word Replace: {word_status}", callback_data=f'toggle_word_replace_{target_id}')],
        [InlineKeyboardButton("Set Word Pairs", callback_data=f'set_word_pairs_{target_id}')],
        [InlineKeyboardButton(f"All Link Replace: {link_status}", callback_data=f'toggle_link_replace_{target_id}')],
        [InlineKeyboardButton("Set Link Replacement", callback_data=f'set_link_replacement_{target_id}')],
        [InlineKeyboardButton("Return", callback_data='return_config')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Function to get list of joined channels as buttons
async def get_channel_list_menu(action, target_id=None):
    if not IS_LOGGED_IN:
        return InlineKeyboardMarkup([[InlineKeyboardButton("Not logged in!", callback_data='noop')]])
    
    try:
        logger.info("Fetching joined channels...")
        dialogs = await client.get_dialogs()
        channels = [d for d in dialogs if d.is_channel]
        if not channels:
            logger.info("No joined channels found.")
            return InlineKeyboardMarkup([[InlineKeyboardButton("No joined channels found!", callback_data='noop')]])
        
        keyboard = []
        for channel in channels:
            channel_id = channel.entity.id
            channel_name = channel.title
            callback_data = f"{action}_{channel_id}_{target_id}" if target_id else f"{action}_{channel_id}"
            keyboard.append([InlineKeyboardButton(channel_name, callback_data=callback_data)])
        keyboard.append([InlineKeyboardButton("Return", callback_data='return_config')])
        logger.info(f"Found {len(channels)} channels.")
        return InlineKeyboardMarkup(keyboard)
    except Exception as e:
        logger.error(f"Error fetching channels: {str(e)}")
        return InlineKeyboardMarkup([[InlineKeyboardButton(f"Error: {str(e)}", callback_data='noop')]])

# Handle /start command to show the menu
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    if not IS_LOGGED_IN:
        await update.message.reply_text("Bot is starting, please wait for terminal login to complete.")
        return
    context.user_data['current_target'] = None  # Reset current target for new config
    await update.message.reply_text(
        "Welcome! Use the buttons below to manage the bot:",
        reply_markup=get_main_menu()
    )

# Handle text messages for setting filters and replacements
async def handle_filter_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    target_id = context.user_data.get('current_target')
    if target_id is None:
        await update.message.reply_text("Please set a target channel first!", reply_markup=get_config_menu())
        return
    config = CONFIGURATIONS[target_id]
    input_text = update.message.text.strip()
    
    if context.user_data.get('awaiting_extensions'):
        config['filters']['extensions'] = [ext.strip().lower() for ext in input_text.split(',')]
        context.user_data['awaiting_extensions'] = False
        await update.message.reply_text(
            f"File extensions set to: {', '.join(config['filters']['extensions'])}",
            reply_markup=get_config_menu(target_id)
        )
    elif context.user_data.get('awaiting_word_pairs'):
        pairs = {}
        for pair in input_text.split(','):
            try:
                old, new = pair.split(':')
                pairs[old.strip().lower()] = new.strip()
            except ValueError:
                await update.message.reply_text("Invalid format! Use 'old:new, old2:new2'")
                return
        config['word_replace']['pairs'] = pairs
        context.user_data['awaiting_word_pairs'] = False
        await update.message.reply_text(
            f"Word replacement pairs set to: {pairs}",
            reply_markup=get_config_menu(target_id)
        )
    elif context.user_data.get('awaiting_link_replacement'):
        config['link_replace']['replacement'] = input_text
        context.user_data['awaiting_link_replacement'] = False
        await update.message.reply_text(
            f"Link replacement set to: {input_text}",
            reply_markup=get_config_menu(target_id)
        )
    else:
        config['filters']['keywords'] = [kw.strip().lower() for kw in input_text.split(',')]
        await update.message.reply_text(
            f"Related words (keywords) set to: {', '.join(config['filters']['keywords'])}",
            reply_markup=get_config_menu(target_id)
        )

# Handle button presses
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not query.from_user or not is_admin(query.from_user.id):
        await query.edit_message_text("You are not authorized to use this bot.")
        return

    if not IS_LOGGED_IN:
        await query.edit_message_text("Please wait for terminal login to complete.", reply_markup=get_main_menu())
        return

    data = query.data.split('_')
    action = data[0]
    target_id = int(data[-1]) if len(data) > 2 and data[-1].isdigit() else context.user_data.get('current_target')

    if action == 'add' and data[1] == 'config':
        context.user_data['current_target'] = None  # Reset for new config
        await query.edit_message_text("Configure your bot settings:", reply_markup=get_config_menu())

    elif action == 'edit':
        target_id = int(data[1])
        if target_id not in CONFIGURATIONS:
            await query.edit_message_text("Configuration not found!", reply_markup=get_main_menu())
            return
        context.user_data['current_target'] = target_id
        await query.edit_message_text(f"Editing configuration for target {target_id}:", reply_markup=get_config_menu(target_id))

    elif action == 'toggle' and data[1] == 'config':
        if target_id is None:
            await query.edit_message_text("Please set a target channel first!", reply_markup=get_config_menu())
            return
        config = CONFIGURATIONS[target_id]
        config['active'] = not config['active']
        await query.edit_message_text(f"Configuration turned {'ON' if config['active'] else 'OFF'}!",
                                      reply_markup=get_config_menu(target_id))

    elif action == 'set' and data[1] == 'source':
        channel_menu = await get_channel_list_menu('source', target_id)
        await query.edit_message_text("Select a source channel to add:", reply_markup=channel_menu)

    elif action == 'set' and data[1] == 'target':
        channel_menu = await get_channel_list_menu('target')
        await query.edit_message_text("Select a target channel:", reply_markup=channel_menu)

    elif action == 'set' and data[1] == 'filters':
        if target_id is None:
            await query.edit_message_text("Please set a target channel first!", reply_markup=get_config_menu())
            return
        await query.edit_message_text("Select message type to filter:", reply_markup=get_filter_type_menu())

    elif action == 'filter':
        if target_id is None:
            await query.edit_message_text("Please set a target channel first!", reply_markup=get_config_menu())
            return
        CONFIGURATIONS.setdefault(target_id, {'active': True, 'sources': [], 'filters': {'type': None, 'keywords': [], 'extensions': []},
                                             'word_replace': {'active': False, 'pairs': {}}, 'link_replace': {'active': False, 'replacement': '[Link Removed]'}})
        config = CONFIGURATIONS[target_id]
        if data[1] == 'text':
            config['filters']['type'] = 'text'
            await query.edit_message_text("Send comma-separated related words (e.g., 'news, update'):", reply_markup=get_config_menu(target_id))
        elif data[1] == 'file':
            config['filters']['type'] = 'file'
            context.user_data['awaiting_extensions'] = True
            await query.edit_message_text("Send comma-separated extensions (e.g., '.pdf, .jpg'):", reply_markup=get_config_menu(target_id))
        elif data[1] == 'both':
            config['filters']['type'] = 'both'
            await query.edit_message_text("Send comma-separated related words (e.g., 'news, update'):", reply_markup=get_config_menu(target_id))
        elif data[1] == 'clear':
            config['filters'] = {'type': None, 'keywords': [], 'extensions': []}
            await query.edit_message_text("All filters cleared!", reply_markup=get_config_menu(target_id))

    elif action == 'word' and data[1] == 'replace':
        if target_id is None:
            await query.edit_message_text("Please set a target channel first!", reply_markup=get_config_menu())
            return
        await query.edit_message_text("Configure word and link replacements:", reply_markup=get_word_replace_menu(target_id))

    elif action == 'toggle' and data[1] == 'word':
        if target_id is None:
            await query.edit_message_text("Please set a target channel first!", reply_markup=get_config_menu())
            return
        config = CONFIGURATIONS[target_id]
        config['word_replace']['active'] = not config['word_replace']['active']
        await query.edit_message_text(f"Word replacement turned {'ON' if config['word_replace']['active'] else 'OFF'}!",
                                      reply_markup=get_word_replace_menu(target_id))

    elif action == 'set' and data[1] == 'word' and data[2] == 'pairs':
        if target_id is None:
            await query.edit_message_text("Please set a target channel first!", reply_markup=get_config_menu())
            return
        context.user_data['awaiting_word_pairs'] = True
        context.user_data['current_target'] = target_id
        await query.edit_message_text("Send word pairs (e.g., '@hello:hi, world:earth'):", reply_markup=get_config_menu(target_id))

    elif action == 'toggle' and data[1] == 'link':
        if target_id is None:
            await query.edit_message_text("Please set a target channel first!", reply_markup=get_config_menu())
            return
        config = CONFIGURATIONS[target_id]
        config['link_replace']['active'] = not config['link_replace']['active']
        await query.edit_message_text(f"Link replacement turned {'ON' if config['link_replace']['active'] else 'OFF'}!",
                                      reply_markup=get_word_replace_menu(target_id))

    elif action == 'set' and data[1] == 'link' and data[2] == 'replacement':
        if target_id is None:
            await query.edit_message_text("Please set a target channel first!", reply_markup=get_config_menu())
            return
        context.user_data['awaiting_link_replacement'] = True
        context.user_data['current_target'] = target_id
        await query.edit_message_text("Send text to replace links (e.g., '[Link Removed]'):", reply_markup=get_config_menu(target_id))

    elif action == 'source':
        if target_id is None:
            await query.edit_message_text("Please set a target channel first!", reply_markup=get_config_menu())
            return
        channel_id = int(data[1])
        full_channel_id = -1000000000000 - channel_id
        CONFIGURATIONS.setdefault(target_id, {'active': True, 'sources': [], 'filters': {'type': None, 'keywords': [], 'extensions': []},
                                             'word_replace': {'active': False, 'pairs': {}}, 'link_replace': {'active': False, 'replacement': '[Link Removed]'}})
        if full_channel_id not in CONFIGURATIONS[target_id]['sources']:
            CONFIGURATIONS[target_id]['sources'].append(full_channel_id)
            await query.edit_message_text(f"Added source channel {full_channel_id}!", reply_markup=get_config_menu(target_id))
        else:
            await query.edit_message_text(f"Channel {full_channel_id} is already a source!", reply_markup=get_config_menu(target_id))

    elif action == 'target':
        channel_id = int(data[1])
        full_channel_id = -1000000000000 - channel_id
        CONFIGURATIONS.setdefault(full_channel_id, {'active': True, 'sources': [], 'filters': {'type': None, 'keywords': [], 'extensions': []},
                                                    'word_replace': {'active': False, 'pairs': {}}, 'link_replace': {'active': False, 'replacement': '[Link Removed]'}})
        context.user_data['current_target'] = full_channel_id
        await query.edit_message_text(f"Target channel set to {full_channel_id}!", reply_markup=get_config_menu(full_channel_id))

    elif action == 'check' and data[1] == 'status':
        if target_id is None:
            await query.edit_message_text("Please set a target channel first!", reply_markup=get_config_menu())
            return
        config = CONFIGURATIONS[target_id]
        source_str = "\n".join([str(ch) for ch in config['sources']]) if config['sources'] else "None"
        filter_type = config['filters']['type'] if config['filters']['type'] else "None"
        keywords_str = ", ".join(config['filters']['keywords']) if config['filters']['keywords'] else "None"
        extensions_str = ", ".join(config['filters']['extensions']) if config['filters']['extensions'] else "None"
        word_replace_str = f"Active: {config['word_replace']['active']}, Pairs: {config['word_replace']['pairs']}"
        link_replace_str = f"Active: {config['link_replace']['active']}, Replacement: {config['link_replace']['replacement']}"
        await query.edit_message_text(
            f"Target Channel: {target_id}\nActive: {config['active']}\nSource Channels:\n{source_str}\n"
            f"Filter Type: {filter_type}\nRelated Words: {keywords_str}\nFile Extensions: {extensions_str}\n"
            f"Word Replace: {word_replace_str}\nLink Replace: {link_replace_str}",
            reply_markup=get_config_menu(target_id)
        )

    elif action == 'return' and data[1] == 'main':
        await query.edit_message_text("Returning to main menu...", reply_markup=get_main_menu())

    elif action == 'return' and data[1] == 'config':
        await query.edit_message_text("Returning to configuration menu...", reply_markup=get_config_menu(target_id))

    elif action == 'noop':
        await query.edit_message_text("No action taken.", reply_markup=get_main_menu())

# Event handler for new messages in source channels
@client.on(events.NewMessage)
async def forward_message(event):
    logger.info(f"New message received from chat {event.chat_id}")
    
    has_text = bool(event.message.message)
    has_file = bool(event.message.media)
    
    for target_id, config in CONFIGURATIONS.items():
        if not config['active']:
            logger.info(f"Configuration for target {target_id} is OFF, skipping...")
            continue
        if event.chat_id not in config['sources']:
            continue
        
        filter_type = config['filters']['type']
        should_forward = False
        message_text = event.message.message or ""  # Includes captions for media

        if filter_type is None:
            should_forward = True
        elif filter_type == 'text' and has_text:
            should_forward = True
        elif filter_type == 'file' and has_file:
            should_forward = True
        elif filter_type == 'both' and (has_text or has_file):
            should_forward = True

        if not should_forward:
            logger.info(f"Message filtered out by type for target {target_id}")
            continue

        # Apply keyword (related words) filter for text or captions
        if has_text and config['filters']['keywords'] and filter_type in ['text', 'both']:
            if not any(kw in message_text.lower() for kw in config['filters']['keywords']):
                logger.info(f"Message filtered out by related words for target {target_id}: {message_text}")
                continue

        # Apply extension filter for files
        if has_file and config['filters']['extensions'] and filter_type in ['file', 'both']:
            if not hasattr(event.message.media, 'document') or not event.message.media.document:
                logger.info(f"Media has no document attribute for target {target_id}")
                continue
            file_name = event.message.media.document.attributes[-1].file_name
            if not any(file_name.lower().endswith(ext) for ext in config['filters']['extensions']):
                logger.info(f"Message filtered out by extensions for target {target_id}: {file_name}")
                continue

        # Process replacements
        if has_text:
            logger.info(f"Original text/caption for target {target_id}: {message_text}")
            if config['word_replace']['active'] and config['word_replace']['pairs']:
                for old, new in config['word_replace']['pairs'].items():
                    pattern = r'(?:\s|^)(@?' + re.escape(old) + r')(?=\s|$)'
                    message_text = re.sub(pattern, ' ' + new, message_text, flags=re.IGNORECASE)
                    logger.info(f"Applied word replacement for target {target_id}: {old} -> {new}, result: {message_text}")
            if config['link_replace']['active']:
                link_pattern = r'(https?://\S+|t\.me/\S+|@\w+)'
                message_text = re.sub(link_pattern, config['link_replace']['replacement'], message_text)
                logger.info(f"Applied link replacement for target {target_id}, result: {message_text}")

        try:
            if has_file and filter_type in [None, 'file', 'both']:
                logger.info(f"Forwarding message with file and caption to {target_id}: {message_text}")
                await client.send_message(target_id, message_text, file=event.message.media)
            elif has_text and filter_type in [None, 'text', 'both']:
                logger.info(f"Forwarding text message to {target_id}: {message_text}")
                await client.send_message(target_id, message_text)
            logger.info(f"Successfully forwarded message {event.message.id} from {event.chat_id} to {target_id}")
        except Exception as e:
            logger.error(f"Error forwarding message to {target_id}: {str(e)}")
            if "FloodWaitError" in str(e):
                wait_time = int(str(e).split("A wait of ")[1].split(" seconds")[0])
                logger.info(f"Waiting {wait_time} seconds due to flood limit...")
                await asyncio.sleep(wait_time)

# Terminal login function
async def terminal_login():
    global IS_LOGGED_IN
    try:
        logger.info("Starting Telegram client login...")
        await client.connect()

        if not await client.is_user_authorized():
            phone = input("Enter your phone number (e.g., +1234567890): ")
            logger.info(f"Phone number entered: {phone}")
            await client.send_code_request(phone)

            code = input("Enter the verification code you received: ")
            logger.info("Code received from user.")
            try:
                await client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                password = input("2FA is enabled. Enter your password: ")
                logger.info("2FA password received.")
                await client.sign_in(phone=phone, password=password)

        IS_LOGGED_IN = True
        logger.info("Terminal login successful!")
    except PhoneNumberInvalidError:
        logger.error("Invalid phone number format. Use +<country_code><number>.")
        raise
    except PhoneCodeInvalidError:
        logger.error("Invalid verification code.")
        raise
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise

# Main function with retry logic for initialization
async def main():
    await terminal_login()

    # Add bot handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_filter_input))

    max_retries = 3
    for attempt in range(max_retries):
        try:
            await application.initialize()
            break
        except TimedOut as e:
            if attempt < max_retries - 1:
                logger.warning(f"Bot initialization timeout, retrying ({attempt + 1}/{max_retries})...")
                await asyncio.sleep(5)
            else:
                raise Exception("Failed to initialize bot after multiple retries") from e

    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    logger.info("Bot started, listening for messages...")
    try:
        await client.run_until_disconnected()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    asyncio.run(main())