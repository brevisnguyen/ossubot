import os
import urllib.parse
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
import oss2

# Cấu hình Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# Đặt level cho thư viện oss2
logging.getLogger('oss2').setLevel(logging.WARNING)

# --- Cấu hình Aliyun OSS (Thay thế bằng thông tin của bạn) ---
# Tốt nhất nên lưu trong biến môi trường hoặc file cấu hình riêng
OSS_ENDPOINT = ""  # Ví dụ: 'oss-cn-hangzhou.aliyuncs.com'
OSS_ACCESS_KEY_ID = ""
OSS_ACCESS_KEY_SECRET = ""
BOT_TOKEN = ""
LOCAL_API_SERVER_URL = "" 
BASE_URL = f"{LOCAL_API_SERVER_URL}/bot"
BASE_FILE_URL = f"{LOCAL_API_SERVER_URL}/file/bot"
OLD_TDLIB_DIR = "/var/lib/telegram-bot-api/"
NEW_HOST_DIR = "./bot-files/"

# Trạng thái cho ConversationHandler
GET_BUCKET_NAME, GET_OSS_FILE_NAME, UPLOADING = range(3)

# Khởi tạo Auth cho OSS
auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)

# Dữ liệu tạm thời để lưu thông tin file và input của người dùng
user_data = {}


def get_host_path_from_url(file_url: str, old_dir_in_container: str, new_dir_on_host: str) -> str:
    """
    Trích xuất đường dẫn file cục bộ từ URL của Local Bot API Server 
    và chuyển đổi nó sang đường dẫn mount trên Host VPS.
    """

    parsed_url = urllib.parse.urlparse(file_url)
    full_path_url = parsed_url.path
    
    start_index = full_path_url.find(old_dir_in_container)
    
    if start_index == -1:
        return f"Error: '{old_dir_in_container}' not found."

    tdlib_local_path = full_path_url[start_index:]
    relative_path_from_tdlib = tdlib_local_path.replace(old_dir_in_container, "", 1)
    
    # 2. Gắn thư mục mới vào phần còn lại của đường dẫn
    # Kết quả sẽ là: bot-files/7616173880:AAGvvBOymP2u9kJfnaPNx3XjQNe8skCW-iI/documents/file_2.zip
    relative_path_to_file = os.path.join(new_dir_on_host, relative_path_from_tdlib.lstrip('/'))
    
    # 3. CHUYỂN THÀNH ĐƯỜNG DẪN TUYỆT ĐỐI (Đây là bước khắc phục lỗi)
    # os.path.abspath(relative_path) sẽ giải quyết đường dẫn tương đối này
    # dựa trên thư mục hiện tại (CWD) và trả về đường dẫn đầy đủ.
    absolute_path_to_file = os.path.abspath(relative_path_to_file)
    
    return f"/www/wwwroot/zzossbotzz{absolute_path_to_file}"


## 🚀 1. Xử lý khi nhận file (tải về cục bộ)
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Nhận file, tải về, và chuyển sang trạng thái hỏi Bucket Name."""
    
    if not update.message.document:
        # await update.message.reply_text("Vui lòng gửi cho tôi một **tệp (file)**.")
        await update.message.reply_text(
            "请发送一个 <b>文件</b> 给我，我将帮您上传到阿里云 OSS。\n\n"
            "支持任意大小（最高 2GB）",
            parse_mode='HTML'
        )
        return ConversationHandler.END

    file_id = update.message.document.file_id
    file_name = update.message.document.file_name or "unknown_file"
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    
    # Tạo thư mục cục bộ: {user_id}/{yyyymmdd}
    today_date = datetime.now().strftime("%Y%m%d")
    download_dir = os.path.join(str(user_id), today_date)
    os.makedirs(download_dir, exist_ok=True)
    
    # Đường dẫn đầy đủ để lưu file
    # local_file_path = os.path.join(download_dir, file_name)

    # Tải file về
    try:
        file_obj = await context.bot.get_file(file_id)
        local_file_path = get_host_path_from_url(
            file_obj.file_path, 
            OLD_TDLIB_DIR, # Bỏ dấu '/' ở đầu/cuối để tìm chính xác hơn
            NEW_HOST_DIR
        )
        
        # Lưu thông tin file vào user_data
        context.user_data["local_file_path"] = local_file_path
        context.user_data["original_file_name"] = file_name
        
        # await update.message.reply_text(
        #     f"✅ Đã tải file **{file_name}** về cục bộ: `{local_file_path}`\n\n"
        #     f"Bây giờ, vui lòng nhập **Bucket Name** của Aliyun OSS bạn muốn upload lên:",
        # )
        await update.message.reply_text(
            (
                "文件已成功下载到本地！\n\n"
                "文件名：<b>{file_name}</b>\n"
                "本地路径：<code>{local_path}</code>\n\n"
                "现在，请输入您要上传到的 <b>阿里云 OSS 存储桶名称</b>（Bucket Name）："
            ).format(
                file_name=file_name,
                local_path=local_file_path
            ),
            parse_mode='HTML'
        )
        # Chuyển sang trạng thái tiếp theo
        return GET_BUCKET_NAME
        
    except Exception as e:
        logging.error(f"Lỗi khi tải file: {e}")
        # await update.message.reply_text(
        #     f"❌ Có lỗi xảy ra trong quá trình tải file. Vui lòng thử lại.{file_obj}"
        # )
        await update.message.reply_text(
            (
                "❌ <b>文件下载失败</b>\n\n"
                "错误原因：<code>{error}</code>\n\n"
                "请稍后重试，或重新发送文件。"
            ).format(error=str(file_obj)),  # Dùng str(file_obj) để hiển thị lỗi
            parse_mode='HTML'
        )
        return ConversationHandler.END

## 📥 2. Hỏi Bucket Name và chuyển sang hỏi tên file OSS
async def get_bucket_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Nhận Bucket Name từ người dùng và chuyển sang trạng thái hỏi tên file OSS."""
    bucket_name = update.message.text.strip()
    
    if not bucket_name:
        # await update.message.reply_text("Bucket Name không được để trống. Vui lòng nhập lại:")
        await update.message.reply_text(
            (
                "⚠️ <b>存储桶名称不能为空！</b>\n\n"
                "请输入有效的 Bucket 名称（例如：<code>my-oss-bucket</code>）："
            ),
            parse_mode='HTML'
        )
        return GET_BUCKET_NAME

    # Lưu Bucket Name
    context.user_data["bucket_name"] = bucket_name
    original_file_name = context.user_data.get("original_file_name", "file.ext")

    # await update.message.reply_text(
    #     f"✅ Đã nhận Bucket Name: **{bucket_name}**\n\n"
    #     f"Bây giờ, vui lòng nhập **tên file** bạn muốn đặt trên OSS (ví dụ: `dir/{original_file_name}`):"
    # )
    await update.message.reply_text(
        (
            "✅ 已确认存储桶：<b>{bucket_name}</b>\n\n"
            "现在，请输入您希望在 OSS 上使用的 <b>文件名</b>（支持路径）：\n"
            "例如：<code>dir/{original_file_name}</code>"
        ).format(
            bucket_name=bucket_name,
            original_file_name=original_file_name  # Đảm bảo biến này có sẵn
        ),
        parse_mode='HTML'
    )
    # Chuyển sang trạng thái tiếp theo
    return GET_OSS_FILE_NAME

## 📌 3. Hỏi tên file OSS và bắt đầu Upload
async def get_oss_file_name_and_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Nhận tên file OSS từ người dùng và bắt đầu quá trình upload."""
    oss_object_name = update.message.text.strip()
    
    if not oss_object_name:
        # await update.message.reply_text("Tên file trên OSS không được để trống. Vui lòng nhập lại:")
        await update.message.reply_text(
            (
                "⚠️ <b>OSS 对象名称不能为空！</b>\n\n"
                "请输入有效的文件名（例如：my_video.mp4）："
            ),
            parse_mode='HTML'
        )
        return GET_OSS_FILE_NAME
        
    local_file_path = context.user_data.get("local_file_path")
    bucket_name = context.user_data.get("bucket_name")
    
    if not local_file_path or not bucket_name:
        # await update.message.reply_text("❌ Lỗi: Thiếu thông tin file hoặc bucket. Vui lòng bắt đầu lại bằng cách gửi file.")
        await update.message.reply_text(
            (
                "❌ <b>错误</b>：缺少文件或存储桶信息。\n\n"
                "请重新发送文件以开始上传流程。"
            ),
            parse_mode='HTML'
        )
        return ConversationHandler.END

    # Lưu tên file OSS
    context.user_data["oss_object_name"] = oss_object_name
    
    # await update.message.reply_text(
    #     f"⏳ Bắt đầu upload file `{os.path.basename(local_file_path)}` lên OSS...\n"
    #     f"Bucket: `{bucket_name}`\n"
    #     f"Object Name: `{oss_object_name}`"
    # )
    await update.message.reply_text(
        (
            "⏳ <b>正在上传文件到阿里云 OSS...</b>\n\n"
            f"文件名：<code>{os.path.basename(local_file_path)}</code>\n"
            f"存储桶：<code>{bucket_name}</code>\n"
            f"对象名称：<code>{oss_object_name}</code>"
        ),
        parse_mode='HTML'
    )
    
    # Chuyển sang trạng thái UPLOADING và gọi hàm upload
    context.job_queue.run_once(
        callback=upload_to_oss_job,
        when=0, # Chạy ngay lập tức
        data={
            "local_path": local_file_path,
            "bucket_name": bucket_name,
            "oss_object_name": oss_object_name,
            "chat_id": update.effective_chat.id,
        },
        name=f"oss_upload_{update.effective_chat.id}",
    )
    
    return UPLOADING

## 📤 4. Hàm thực hiện Upload lên OSS (Chạy trong Job Queue)
async def upload_to_oss_job(context: ContextTypes.DEFAULT_TYPE):
    """Job queue callback để thực hiện upload file lên Aliyun OSS."""
    job_data = context.job.data
    
    local_path = job_data["local_path"]
    bucket_name = job_data["bucket_name"]
    oss_object_name = job_data["oss_object_name"]
    chat_id = job_data["chat_id"]
    
    try:
        # Khởi tạo Bucket
        bucket = oss2.Bucket(auth, OSS_ENDPOINT, bucket_name, is_cname=True)

        # Upload file đơn giản (Simple Upload)
        # put_object_from_file sẽ đọc file từ local_path và upload lên oss_object_name
        bucket.put_object_from_file(oss_object_name, local_path)
        
        # Lấy URL công khai (nếu bucket có quyền public-read)
        file_url = f"https://{OSS_ENDPOINT}/{oss_object_name}"
        
        # await context.bot.send_message(
        #     chat_id=chat_id,
        #     text=f"🎉 **File đã được upload lên OSS thành công!**\n\n"
        #          f"Bucket: `{bucket_name}`\n"
        #          f"Object Name: `{oss_object_name}`\n"
        #          f"URL (nếu công khai): [Tải xuống]({file_url})",
        #          parse_mode="Markdown"
        # )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🎉 <b>文件已成功上传到阿里云 OSS！</b>\n\n"
                f"存储桶：<code>{bucket_name}</code>\n"
                f"对象名称：<code>{oss_object_name}</code>\n"
                f"下载链接：<a href=\"{file_url}\">点击下载</a>"
            ),
            parse_mode='HTML',
            disable_web_page_preview=True  # Tắt preview link (gọn hơn)
        )
        
    except oss2.exceptions.NoSuchBucket:
        # await context.bot.send_message(
        #     chat_id=chat_id,
        #     text=f"❌ Lỗi OSS: **Bucket `{bucket_name}` không tồn tại** hoặc Endpoint `{OSS_ENDPOINT}` không đúng. Vui lòng kiểm tra lại."
        # )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ <b>阿里云 OSS 错误</b>：\n"
                f"存储桶 <code>{bucket_name}</code> 不存在，或 Endpoint <code>{OSS_ENDPOINT}</code> 配置错误。\n\n"
                "请检查：\n"
                "• Bucket 名称是否拼写正确\n"
                "• Endpoint 是否匹配 Bucket 所在地区（如 <code>oss-cn-hangzhou.aliyuncs.com</code>）\n"
                "• 网络是否能访问阿里云 OSS"
            ),
            parse_mode='HTML'
        )
    except oss2.exceptions.AccessDenied:
        # await context.bot.send_message(
        #     chat_id=chat_id,
        #     text=f"❌ Lỗi OSS: **Truy cập bị từ chối** (Access Denied). Kiểm tra **Access Key, Secret** và **quyền** của người dùng."
        # )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ <b>阿里云 OSS 错误</b>：访问被拒绝（Access Denied）\n\n"
                "请检查以下内容：\n"
                "• <b>AccessKey ID</b> 和 <b>AccessKey Secret</b> 是否正确\n"
                "• OSS <b>Bucket 权限</b> 是否已授权给该用户\n"
                "• 是否设置了正确的 <b>Endpoint</b>（如 oss-cn-hangzhou.aliyuncs.com）"
            ),
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Lỗi khi upload lên OSS: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ 上传到阿里云 OSS 时发生未知错误：\n\n<code>{e}</code>"
        )
        # Xóa file đã tải về sau khi upload xong (tùy chọn, để tiết kiệm dung lượng)
        # try:
        #     os.remove(local_path)
        #     # Xóa thư mục nếu trống
        #     download_dir = os.path.dirname(local_path)
        #     if not os.listdir(download_dir):
        #         os.rmdir(download_dir)
        #         parent_dir = os.path.dirname(download_dir)
        #         if not os.listdir(parent_dir):
        #             os.rmdir(parent_dir)
        # except Exception as e:
        #     logging.warning(f"Không thể xóa file cục bộ {local_path}: {e}")
            
    # Kết thúc hội thoại sau khi upload hoặc gặp lỗi
    return ConversationHandler.END


## 🛑 Hàm hủy (kết thúc hội thoại)
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Hủy và kết thúc hội thoại."""
    await update.message.reply_text(
        "已取消文件上传。❌\n"
        "您可以发送其他文件重新开始上传到阿里云 OSS。📤"
    )
    # Xóa file đã tải về nếu tồn tại (tùy chọn)
    # local_path = context.user_data.get("local_file_path")
    # if local_path and os.path.exists(local_path):
    #     try:
    #         os.remove(local_path)
    #         # Tùy chọn: Xóa thư mục rỗng
    #         download_dir = os.path.dirname(local_path)
    #         if not os.listdir(download_dir):
    #             os.rmdir(download_dir)
    #     except Exception as e:
    #         logging.warning(f"Không thể xóa file cục bộ khi hủy: {e}")
    context.user_data.clear()
    return ConversationHandler.END

## ⚙️ Hàm main để chạy Bot
def main() -> None:
    """Khởi chạy Bot."""
    # Tạo ứng dụng và truyền token
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .base_url(BASE_URL)
        .base_file_url(BASE_FILE_URL)
        .build()
    )

    # Định nghĩa ConversationHandler
    file_upload_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.ATTACHMENT | filters.Document.ALL, handle_document)],

        states={
            GET_BUCKET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_bucket_name)],
            GET_OSS_FILE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_oss_file_name_and_upload)],
            UPLOADING: [MessageHandler(filters.TEXT | filters.COMMAND, lambda u, c: ConversationHandler.END)], # Không làm gì khi đang upload
        },

        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Thêm handler vào ứng dụng
    application.add_handler(file_upload_handler)
    application.add_handler(
        CommandHandler(
            "start",
            lambda update, context: update.message.reply_text(
                "您好！👋\n"
                "请发送文件，我将帮您上传到阿里云 OSS 云存储。\n\n"
                "支持任意大小文件（最高 2GB）🚀\n"
                "上传后会返回下载链接 🔗"
            )
        )
    )

    # Bắt đầu polling
    print("Bot đang chạy...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, timeout=120)


if __name__ == "__main__":
    main()