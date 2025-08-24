from telegram import KeyboardButton, ReplyKeyboardMarkup, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
import os
from dotenv import load_dotenv
from db import AsyncSessionLocal
from sqlalchemy import select
from models import ReferralCode, ReferralCodeProductEnum, Seller
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from kavenegar import KavenegarAPI
import re
import random
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

PRODUCT_MAP = {
    "محصولات الماس": ReferralCodeProductEnum.ALMAS,
    "پایه 5ام": ReferralCodeProductEnum.GRADE_5,
    "پایه 6ام": ReferralCodeProductEnum.GRADE_6,
    "پایه 7ام": ReferralCodeProductEnum.GRADE_7,
    "پایه 8ام": ReferralCodeProductEnum.GRADE_8,
    "پایه 9ام": ReferralCodeProductEnum.GRADE_9
}

(ASK_NAME, ASK_PHONE, ASK_OTP) = range(3)

(ASK_CODE, ASK_DISCOUNT, ASK_PRODUCT, ASK_INSTALLMENT) = range(4)

def is_valid_persian_name(name: str) -> bool:
    """Check if the name is a valid Persian name (2-5 words, 5-50 characters)"""
    return bool(re.fullmatch(r"[آ-ی\s]{5,50}", name.strip()))

def is_valid_phone(number: str) -> bool:
    """Check if the phone number is valid (09xxxxxxxxx format)"""
    return bool(re.fullmatch(r"09\d{9}", number))

async def register_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the registration process"""
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    
    async with AsyncSessionLocal() as session:
        telegram_id = update.effective_user.id
        result = await session.execute(select(Seller).where(Seller.telegram_id == telegram_id))
        seller = result.scalar_one_or_none()
        
        if seller:
            await update.message.reply_text("شما قبلاً ثبت‌نام کردید ✅")
            return ConversationHandler.END
    
    await update.message.reply_text("👤 لطفاً نام و نام خانوادگی خود را به فارسی وارد کنید:\nانصراف : /cancel")
    return ASK_NAME

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle name input"""
    if not update.message or not update.message.text:
        return ASK_NAME
    
    name = update.message.text.strip()
    if not is_valid_persian_name(name):
        await update.message.reply_text("❌ لطفاً نام و نام خانوادگی را به‌درستی و به زبان فارسی وارد کنید.")
        return ASK_NAME
    
    if context.user_data is None:
        context.user_data = {}
    context.user_data["name"] = name
    
    await update.message.reply_text("📱 لطفاً شماره موبایل خود را وارد کنید (مثال: 09123456789):\nانصراف : /cancel")
    return ASK_PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle phone number input and send OTP"""
    if not update.message or not update.message.text:
        return ASK_PHONE
    
    phone = update.message.text.strip()
    
    # Convert Persian digits to English
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    english_digits = "0123456789"
    trans_table = str.maketrans(persian_digits, english_digits)
    phone = phone.translate(trans_table)
    
    if not is_valid_phone(phone):
        await update.message.reply_text("❌ شماره وارد شده معتبر نیست. لطفاً شماره را به صورت صحیح وارد کنید.")
        return ASK_PHONE

    if context.user_data is None:
        context.user_data = {}
    context.user_data["phone"] = phone

    # Generate OTP
    otp = str(random.randint(1000, 9999))
    context.user_data["otp"] = otp

    # Send OTP via Kavenegar
    try:
        api = KavenegarAPI(os.getenv("KAVENEGAR_API_KEY"))
        api.verify_lookup({
            "receptor": phone,
            "token": otp,
            "template": "verify",
            "type": "sms"
        })
        await update.message.reply_text("✅ کد تایید پیامک شد. لطفاً کد را وارد کنید:")
        return ASK_OTP
    except Exception as e:
        await update.message.reply_text(f"خطا در ارسال پیامک: {e}")
        return ConversationHandler.END

async def handle_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle OTP verification and complete registration"""
    if not update.message or not update.message.text or not update.effective_user:
        return ASK_OTP
    
    code = update.message.text.strip()
    
    # Convert Persian digits to English
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    english_digits = "0123456789"
    trans_table = str.maketrans(persian_digits, english_digits)
    code = code.translate(trans_table)
    
    if context.user_data is None or code != context.user_data.get("otp"):
        await update.message.reply_text("❌ کد وارد شده صحیح نیست. لطفا دوباره تلاش کنید:")
        return ASK_OTP

    name = context.user_data["name"]
    phone = context.user_data["phone"]
    telegram_id = update.effective_user.id
    username = update.effective_user.username or ""
    
    async with AsyncSessionLocal() as session:
        # Check if seller already exists
        existing_seller = await session.execute(select(Seller).where(Seller.telegram_id == telegram_id))
        seller = existing_seller.scalar_one_or_none()

        if seller:
            # Update existing seller
            seller.name = name
            seller.number = phone
            seller.username = username
            seller.updated_at = datetime.now()
        else:
            # Create new seller
            seller = Seller(
                telegram_id=telegram_id,
                username=username,
                number=phone,
                name=name
            )
            session.add(seller)

        await session.commit()

    await update.message.reply_text("🎉 ثبت‌نام شما با موفقیت انجام شد!")
    await start(update, context)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the registration process"""
    if update.message:
        await update.message.reply_text("فرآیند لغو شد.")
        await start(update, context)
    return ConversationHandler.END

async def code_details(code_id: int):
    async with AsyncSessionLocal() as session:
        code = await session.get(ReferralCode, code_id)
        return code

async def inline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data.startswith("code_"):
        code_id = int(query.data.split("_")[1])
        code = await code_details(code_id)
        await query.answer()
        keyboard = [[InlineKeyboardButton("حذف", callback_data=f"delete_code_{code_id}"), InlineKeyboardButton("بازگشت", callback_data=f"list_codes")]]
        await query.edit_message_text(f"کد {code.code} \n\n مقدار تخفیف {code.discount}هزار تومان \n\n محصول {code.product} \n\n قابلیت قسطی {code.installment} \n\n تاریخ ایجاد: {code.created_at} \n\n", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data.startswith("delete_code_"):
        async with AsyncSessionLocal() as session:
            user = await session.execute(select(Seller).where(Seller.telegram_id == query.from_user.id))
            seller = user.scalar_one_or_none()
            code_id = int(query.data.split("_")[2])
            code = await session.get(ReferralCode, code_id)
            if code.owner_id != seller.id:
                await query.answer("شما اجازه حذف این کد را ندارید")
                return
            await session.delete(code)
            await session.commit()
        await query.answer()
        await query.edit_message_text("کد با موفقیت حذف شد")
        return await list_codes_func(update, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("به ربات نمایندگان ماز خوش امدید برای استفاده از ربات میتونید از دستورات بخش Menu استفاده کنید یا از دستور /help برای دریافت راهنمایی استفاده کنید \n\n/help راهنمایی ")

async def help_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("برای استفاده از ربات میتونید از دستورات زیر استفاده کنید \n\n/start شروع ربات \n /list_codes لیست کد ها و مدیریت کد ها\n /add_code اضافه کردن کد \n /register ثبت نام\n /help راهنمایی")

async def list_codes_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with AsyncSessionLocal() as session:
        user = await session.execute(select(Seller).where(Seller.telegram_id == update.message.from_user.id))
        seller = user.scalar_one_or_none()
        if seller:
            result = await session.execute(select(ReferralCode).where(ReferralCode.owner_id == seller.id))
            codes = result.scalars().all()
            keyboard = [[InlineKeyboardButton(code.code, callback_data=f"code_{code.id}")] for code in codes]
            await update.message.reply_text("لیست کد های شما", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text("شما هنوز ثبت نام نکرده اید لطفا برای ثبت نام از دستور /register استفاده کنید")
            return start(update, context)

async def add_code_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    async with AsyncSessionLocal() as session:
        seller = await session.execute(select(Seller).where(Seller.telegram_id == user.id))
        seller = seller.scalar_one_or_none()
        if not seller:
            await update.message.reply_text("شما هنوز ثبت نام نکرده اید لطفا برای ثبت نام از دستور /register استفاده کنید")
            return start(update, context)
    await update.message.reply_text("لطفا کد را وارد کنید ، کد میتواند تلفیقی از حروف و اعداد و یا فقط حرف و عدد باشد(حداقل 5 کاراکتر انگلیسی). \n\n/cancel لغو")
    return ASK_CODE

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text
    if len(code) < 5:
        await update.message.reply_text("کد باید حداقل 5 کاراکتر باشد")
        return ASK_CODE
    if not code.isalpha() and not code.isdigit():
        await update.message.reply_text("کد باید تلفیق و یا فقط حروف و اعداد انگلیسی باشد")
        return ASK_CODE
    async with AsyncSessionLocal() as session:
        code_check = await session.execute(select(ReferralCode).where(ReferralCode.code == code))
        code_check = code_check.scalar_one_or_none()
        if code_check:
            await update.message.reply_text("کد تکراری است لطفا کد دیگری وارد کنید")
            return ASK_CODE
    await update.message.reply_text("لطفا مقدار تخفیف را وارد کنید(تا 1,500,000 تومان برای خرید نقدی و 1,000,000 تومان برای خرید قسطی)")
    context.user_data["code"] = code
    return ASK_DISCOUNT

async def handle_discount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    discount = update.message.text
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    english_digits = "0123456789"
    trans_table = str.maketrans(persian_digits, english_digits)
    discount = discount.translate(trans_table)
    if not discount.isdigit():
        await update.message.reply_text("مقدار تخفیف باید عدد باشد")
        return ASK_DISCOUNT
    if int(discount) > 1500000:
        await update.message.reply_text("مقدار تخفیف باید کمتر از 1,500,000 تومان باشد")
        return ASK_DISCOUNT
    keyboard = [[KeyboardButton("محصولات الماس"), KeyboardButton("پایه 5ام")], [KeyboardButton("پایه 6ام"), KeyboardButton("پایه 7ام")], [KeyboardButton("پایه 8ام"), KeyboardButton("پایه 9ام")]]
    keyboard_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("لطفا محصول یا محصولاتی که میخواهید کد را به آن اختصاص دهید انتخاب کنید", reply_markup=keyboard_markup)
    context.user_data["discount"] = discount
    return ASK_PRODUCT

async def handle_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product = update.message.text
    valid_products = ["محصولات الماس", "پایه 5ام", "پایه 6ام", "پایه 7ام", "پایه 8ام", "پایه 9ام"]
    if product not in valid_products:
        await update.message.reply_text("محصول انتخاب شده معتبر نیست.")
        return ASK_PRODUCT
    if product == "محصولات الماس" and int(context.user_data["discount"]) > 1000000:
        await update.message.reply_text("محصولات الماس فقط برای تخفیف زیر 1,000,000 تومان میباشد")
        return ASK_PRODUCT
    
    keyboard = [[KeyboardButton("قسطی"), KeyboardButton("نقدی")]]
    await update.message.reply_text("لطفا قابلیت قسطی را انتخاب کنید\n نکته:قابلیت قسطی فقط برای محصولات الماس و تخفیف زیر 1,000,000 تومان میباشد\n\n/cancel لغو", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    context.user_data["product"] = product
    return ASK_INSTALLMENT

async def handle_installment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    installment = update.message.text
    if installment == "قسطی":
        if context.user_data["product"] != ReferralCodeProductEnum.ALMAS or int(context.user_data["discount"]) > 1000000:
            await update.message.reply_text("❌ فقط محصولات الماس با تخفیف زیر 1,000,000 تومان می‌توانند قسطی باشند.")
            return ASK_INSTALLMENT
        context.user_data["installment"] = True
    else:
        context.user_data["installment"] = False
    async with AsyncSessionLocal() as session:
        seller = await session.execute(select(Seller).where(Seller.telegram_id == update.message.from_user.id))
        seller = seller.scalar_one_or_none()
        if not seller:
            await update.message.reply_text("شما هنوز ثبت نام نکرده اید لطفا برای ثبت نام از دستور /register استفاده کنید")
            return start(update, context)
        enum_product = PRODUCT_MAP[context.user_data["product"]]
        code = ReferralCode(
            code=context.user_data["code"],
            discount=int(context.user_data["discount"]),
            product=enum_product,
            installment=context.user_data["installment"],
            owner_id=seller.id
        )
        session.add(code)
        await session.commit()
        await update.message.reply_text("کد با موفقیت اضافه شد لطفا برای اضافه کردن کد دیگری از دستور /add_code استفاده کنید")
        return list_codes_func(update, context)

if __name__ == "__main__":
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    app = ApplicationBuilder().token(str(BOT_TOKEN)).build()
    logger.info("Bot started")
    
    
    # Registration conversation handler
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("register", register_func)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            ASK_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_otp)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_code", add_code_func)],
        states={
            ASK_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code)],
            ASK_DISCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_discount)],
            ASK_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product)],
            ASK_INSTALLMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_installment)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)]
    ))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_func))
    app.add_handler(CommandHandler("list_codes", list_codes_func))
    app.add_handler(CommandHandler("add_code", add_code_func))
    app.add_handler(CallbackQueryHandler(inline_handler))
    app.run_polling()