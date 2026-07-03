import html
import json
import logging
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from typing import Any


def load_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_env()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "qyabape69").lstrip("@")
DB_PATH = os.getenv("DB_PATH", "shop.db")
DEFAULT_START_STICKER = os.getenv("START_STICKER_ID", "")
DEFAULT_CATALOG_STICKER = os.getenv("CATALOG_STICKER_ID", "")
DEFAULT_FAVORITES_STICKER = os.getenv("FAVORITES_STICKER_ID", "")
ADMIN_IDS = {
    int(admin_id.strip())
    for admin_id in os.getenv("ADMIN_IDS", "").split(",")
    if admin_id.strip().isdigit()
}
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


@dataclass
class Product:
    id: int
    title: str
    price: str
    sizes: str
    description: str
    photo_file_id: str | None
    is_active: bool
    section: str


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(db()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                price TEXT NOT NULL,
                sizes TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                photo_file_id TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                section TEXT NOT NULL DEFAULT 'catalog',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, product_id)
            );

            CREATE TABLE IF NOT EXISTS states (
                user_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()]
        if "section" not in columns:
            conn.execute("ALTER TABLE products ADD COLUMN section TEXT NOT NULL DEFAULT 'catalog'")
        conn.commit()


def request(method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload or {}).encode("utf-8")
    req = urllib.request.Request(f"{API_URL}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result)
    return result


def send_message(chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    request("sendMessage", payload)


def send_photo(chat_id: int, photo: str, caption: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    request("sendPhoto", payload)


def send_sticker(chat_id: int, sticker: str) -> None:
    request("sendSticker", {"chat_id": chat_id, "sticker": sticker})


def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    request("editMessageText", payload)


def edit_reply_markup(chat_id: int, message_id: int, reply_markup: dict[str, Any]) -> None:
    request(
        "editMessageReplyMarkup",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
        },
    )


def answer_callback(callback_id: str, text: str = "") -> None:
    request("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def main_menu(user_id: int) -> dict[str, Any]:
    keyboard = [
        [{"text": "🛍 Каталог"}, {"text": "✅ В наличии"}],
        [{"text": "♡ Избранное"}],
        [{"text": "💬 Связаться"}],
    ]
    if user_id in ADMIN_IDS:
        keyboard.append([{"text": "⚙ Админка"}])
    return {"keyboard": keyboard, "resize_keyboard": True}


def inline_keyboard(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}


def admin_menu() -> dict[str, Any]:
    return inline_keyboard(
        [
            [{"text": "＋ Добавить в каталог", "callback_data": "admin:add:catalog"}],
            [{"text": "＋ Добавить в наличие", "callback_data": "admin:add:available"}],
            [{"text": "✎ Управлять товарами", "callback_data": "admin:list"}],
        ]
    )


def get_setting(key: str, default: str = "") -> str:
    with closing(db()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with closing(db()) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def clear_setting(key: str) -> None:
    with closing(db()) as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()


def send_optional_sticker(chat_id: int, key: str, default: str = "") -> None:
    sticker = get_setting(key, default)
    if sticker:
        send_sticker(chat_id, sticker)


def product_from_row(row: sqlite3.Row) -> Product:
    return Product(
        id=row["id"],
        title=row["title"],
        price=row["price"],
        sizes=row["sizes"],
        description=row["description"],
        photo_file_id=row["photo_file_id"],
        is_active=bool(row["is_active"]),
        section=row["section"],
    )


def get_product(product_id: int) -> Product | None:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return product_from_row(row) if row else None


def get_products(active_only: bool = False, section: str | None = None) -> list[Product]:
    sql = "SELECT * FROM products"
    filters = []
    params = []
    if active_only:
        filters.append("is_active = 1")
    if section:
        filters.append("section = ?")
        params.append(section)
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY created_at DESC, id DESC"
    with closing(db()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [product_from_row(row) for row in rows]


def get_favorites(user_id: int) -> list[Product]:
    with closing(db()) as conn:
        rows = conn.execute(
            """
            SELECT p.*
            FROM products p
            JOIN favorites f ON f.product_id = p.id
            WHERE f.user_id = ? AND p.is_active = 1
            ORDER BY f.created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [product_from_row(row) for row in rows]


def is_favorite(user_id: int, product_id: int) -> bool:
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        ).fetchone()
    return bool(row)


def toggle_favorite(user_id: int, product_id: int) -> bool:
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        ).fetchone()
        if row:
            conn.execute("DELETE FROM favorites WHERE user_id = ? AND product_id = ?", (user_id, product_id))
            conn.commit()
            return False
        conn.execute("INSERT OR IGNORE INTO favorites (user_id, product_id) VALUES (?, ?)", (user_id, product_id))
        conn.commit()
        return True


def product_text(product: Product) -> str:
    status = "" if product.is_active else "\n\n<b>Статус:</b> скрыт"
    return (
        f"<b>{html.escape(product.title)}</b>\n\n"
        f"<b>Цена:</b> {html.escape(product.price)}\n"
        f"<b>Размеры:</b> {html.escape(product.sizes or 'уточняйте')}\n\n"
        f"{html.escape(product.description or 'Описание скоро появится.')}"
        f"{status}"
    )


def product_keyboard(product_id: int, user_id: int, show_back: bool = True) -> dict[str, Any]:
    fav_text = "♡ Убрать из избранного" if is_favorite(user_id, product_id) else "♡ В избранное"
    rows = [
        [{"text": fav_text, "callback_data": f"fav:{product_id}"}],
        [{"text": "💬 Написать продавцу", "url": f"https://t.me/{OWNER_USERNAME}"}],
    ]
    if show_back:
        rows.append([{"text": "← Назад к каталогу", "callback_data": "catalog:list"}])
    return inline_keyboard(rows)


def catalog_keyboard(products: list[Product]) -> dict[str, Any]:
    rows = [[{"text": f"• {product.title}", "callback_data": f"product:{product.id}"}] for product in products]
    rows.append([{"text": "💬 Связаться с продавцом", "url": f"https://t.me/{OWNER_USERNAME}"}])
    return inline_keyboard(rows)


def admin_product_keyboard(product: Product) -> dict[str, Any]:
    active_text = "Скрыть из каталога" if product.is_active else "Показать в каталоге"
    return inline_keyboard(
        [
            [
                {"text": "Название", "callback_data": f"edit:{product.id}:title"},
                {"text": "Цена", "callback_data": f"edit:{product.id}:price"},
            ],
            [
                {"text": "Размеры", "callback_data": f"edit:{product.id}:sizes"},
                {"text": "Описание", "callback_data": f"edit:{product.id}:description"},
            ],
            [{"text": "Фото", "callback_data": f"edit:{product.id}:photo"}],
            [
                {"text": active_text, "callback_data": f"toggle:{product.id}"},
                {"text": "Удалить", "callback_data": f"delete:{product.id}:ask"},
            ],
            [{"text": "Перенести в наличие" if product.section == "catalog" else "Перенести в каталог", "callback_data": f"section:{product.id}"}],
            [{"text": "Назад", "callback_data": "admin:list"}],
        ]
    )


def get_state(user_id: int) -> tuple[str | None, dict[str, Any]]:
    with closing(db()) as conn:
        row = conn.execute("SELECT state, data FROM states WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None, {}
    return row["state"], json.loads(row["data"])


def set_state(user_id: int, state: str, data: dict[str, Any] | None = None) -> None:
    with closing(db()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO states (user_id, state, data) VALUES (?, ?, ?)",
            (user_id, state, json.dumps(data or {}, ensure_ascii=False)),
        )
        conn.commit()


def clear_state(user_id: int) -> None:
    with closing(db()) as conn:
        conn.execute("DELETE FROM states WHERE user_id = ?", (user_id,))
        conn.commit()


def send_catalog(chat_id: int) -> None:
    products = get_products(active_only=True, section="catalog")
    if not products:
        send_optional_sticker(chat_id, "catalog_sticker", DEFAULT_CATALOG_STICKER)
        send_message(chat_id, "Каталог пока пуст. Скоро здесь появятся вещи.")
        return
    send_optional_sticker(chat_id, "catalog_sticker", DEFAULT_CATALOG_STICKER)
    send_message(
        chat_id,
        "<b>🛍 Каталог</b>\n\nВыберите товар, чтобы посмотреть фото, цену, размеры и описание:",
        catalog_keyboard(products),
    )


def send_available(chat_id: int) -> None:
    products = get_products(active_only=True, section="available")
    if not products:
        send_optional_sticker(chat_id, "catalog_sticker", DEFAULT_CATALOG_STICKER)
        send_message(chat_id, "Сейчас нет вещей в наличии. Скоро добавим новые позиции.")
        return
    send_optional_sticker(chat_id, "catalog_sticker", DEFAULT_CATALOG_STICKER)
    send_message(
        chat_id,
        "<b>✅ В наличии</b>\n\nВсе вещи, которые сейчас можно купить:",
        catalog_keyboard(products),
    )


def show_product(chat_id: int, user_id: int, product: Product) -> None:
    if product.photo_file_id:
        send_photo(chat_id, product.photo_file_id, product_text(product), product_keyboard(product.id, user_id))
    else:
        send_message(chat_id, product_text(product), product_keyboard(product.id, user_id))


def send_favorites(chat_id: int, user_id: int) -> None:
    products = get_favorites(user_id)
    if not products:
        send_optional_sticker(chat_id, "favorites_sticker", DEFAULT_FAVORITES_STICKER)
        send_message(chat_id, "В избранном пока пусто. Откройте каталог и добавьте понравившиеся вещи.")
        return
    send_optional_sticker(chat_id, "favorites_sticker", DEFAULT_FAVORITES_STICKER)
    send_message(chat_id, "<b>♡ Избранное</b>\n\nВыберите товар:", catalog_keyboard(products))


def send_admin_products(chat_id: int, message_id: int | None = None) -> None:
    products = get_products()
    if not products:
        text = "Товаров пока нет. Добавьте первый товар в каталог или в наличие."
        if message_id:
            edit_message_text(chat_id, message_id, text, admin_menu())
        else:
            send_message(chat_id, text, admin_menu())
        return
    rows = []
    for product in products:
        marker = "в каталоге" if product.is_active else "скрыт"
        section = "каталог" if product.section == "catalog" else "в наличии"
        rows.append([{"text": f"{product.title} · {section} · {marker}", "callback_data": f"admin:product:{product.id}"}])
    rows.append([{"text": "Добавить в каталог", "callback_data": "admin:add:catalog"}])
    rows.append([{"text": "Добавить в наличие", "callback_data": "admin:add:available"}])
    text = "<b>Товары</b>\n\nВыберите товар для редактирования:"
    if message_id:
        edit_message_text(chat_id, message_id, text, inline_keyboard(rows))
    else:
        send_message(chat_id, text, inline_keyboard(rows))


def handle_state(chat_id: int, user_id: int, message: dict[str, Any], state: str, data: dict[str, Any]) -> None:
    text = (message.get("text") or "").strip()
    photos = message.get("photo") or []
    sticker = message.get("sticker")
    if text == "/cancel":
        clear_state(user_id)
        send_message(chat_id, "Действие отменено.", main_menu(user_id))
        return
    if state.startswith("add:"):
        step = state.split(":", 1)[1]
        if step in {"title", "price", "sizes", "description"}:
            if not text:
                send_message(chat_id, "Отправьте текст.")
                return
            data[step] = text
            next_steps = {
                "title": ("price", "Введите цену, например: 25000 AMD или 60$:"),
                "price": ("sizes", "Введите размеры, например: S, M, L:"),
                "sizes": ("description", "Введите описание товара:"),
                "description": ("photo", "Отправьте фото товара или напишите -, если фото нет."),
            }
            next_step, prompt = next_steps[step]
            set_state(user_id, f"add:{next_step}", data)
            send_message(chat_id, prompt)
            return
        if step == "photo":
            photo_file_id = photos[-1]["file_id"] if photos else None
            if not photos and text != "-":
                send_message(chat_id, "Отправьте фото или напишите -.")
                return
            with closing(db()) as conn:
                conn.execute(
                    """
                    INSERT INTO products (title, price, sizes, description, photo_file_id, section)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["title"],
                        data["price"],
                        data["sizes"],
                        data["description"],
                        photo_file_id,
                        data.get("section", "catalog"),
                    ),
                )
                conn.commit()
            clear_state(user_id)
            send_message(chat_id, "Товар добавлен.", main_menu(user_id))
            return
    if state == "edit":
        field = data["field"]
        product_id = int(data["product_id"])
        if field == "photo":
            if photos:
                value = photos[-1]["file_id"]
            elif text == "-":
                value = None
            else:
                send_message(chat_id, "Отправьте новое фото или -, чтобы удалить фото.")
                return
            sql = "UPDATE products SET photo_file_id = ? WHERE id = ?"
        else:
            if not text:
                send_message(chat_id, "Отправьте текст.")
                return
            value = text
            sql = f"UPDATE products SET {field} = ? WHERE id = ?"
        with closing(db()) as conn:
            conn.execute(sql, (value, product_id))
            conn.commit()
        clear_state(user_id)
        send_message(chat_id, "Товар обновлен.")
        send_admin_products(chat_id)
    if state == "set_sticker":
        if not sticker:
            send_message(chat_id, "Отправьте именно стикер. Для отмены напишите /cancel.")
            return
        set_setting(data["key"], sticker["file_id"])
        clear_state(user_id)
        send_message(chat_id, "Стикер сохранен. Теперь бот будет отправлять его в этом разделе.", main_menu(user_id))


def handle_message(message: dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = (message.get("text") or "").strip()
    state, data = get_state(user_id)
    if state:
        handle_state(chat_id, user_id, message, state, data)
        return
    if text == "/start":
        clear_state(user_id)
        send_optional_sticker(chat_id, "start_sticker", DEFAULT_START_STICKER)
        send_message(
            chat_id,
            f"<b>Магазин одежды</b>\n\nСтильные вещи в наличии, избранное и быстрая связь с продавцом: @{OWNER_USERNAME}",
            main_menu(user_id),
        )
    elif text in {"/catalog", "Каталог", "🛍 Каталог"}:
        send_catalog(chat_id)
    elif text in {"/available", "В наличии", "✅ В наличии"}:
        send_available(chat_id)
    elif text in {"/favorites", "Избранное", "♡ Избранное"}:
        send_favorites(chat_id, user_id)
    elif text in {"/contact", "Связаться", "💬 Связаться"}:
        send_message(
            chat_id,
            f"Напишите мне в Telegram: @{OWNER_USERNAME}",
            inline_keyboard([[{"text": "💬 Открыть чат", "url": f"https://t.me/{OWNER_USERNAME}"}]]),
        )
    elif text in {"/admin", "Админка", "⚙ Админка"}:
        if user_id not in ADMIN_IDS:
            send_message(chat_id, "Эта команда доступна только администратору.")
            return
        send_message(
            chat_id,
            "<b>⚙ Админка магазина</b>\n\nЧто делаем?\n\nСтикеры: /set_start_sticker, /set_catalog_sticker, /set_favorites_sticker",
            admin_menu(),
        )
    elif text in {"/set_start_sticker", "/set_catalog_sticker", "/set_favorites_sticker"}:
        if user_id not in ADMIN_IDS:
            send_message(chat_id, "Эта команда доступна только администратору.")
            return
        key_by_command = {
            "/set_start_sticker": "start_sticker",
            "/set_catalog_sticker": "catalog_sticker",
            "/set_favorites_sticker": "favorites_sticker",
        }
        set_state(user_id, "set_sticker", {"key": key_by_command[text]})
        send_message(chat_id, "Отправьте красивый стикер, который нужно показывать в этом разделе.")
    elif text == "/clear_stickers":
        if user_id not in ADMIN_IDS:
            send_message(chat_id, "Эта команда доступна только администратору.")
            return
        clear_setting("start_sticker")
        clear_setting("catalog_sticker")
        clear_setting("favorites_sticker")
        send_message(chat_id, "Все сохраненные стикеры очищены.", main_menu(user_id))
    elif text == "/cancel":
        clear_state(user_id)
        send_message(chat_id, "Действие отменено.", main_menu(user_id))
    else:
        send_message(chat_id, "Выберите действие в меню.", main_menu(user_id))


def handle_callback(callback: dict[str, Any]) -> None:
    callback_id = callback["id"]
    user_id = callback["from"]["id"]
    message = callback["message"]
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    data = callback["data"]
    if data == "catalog:list":
        send_catalog(chat_id)
        answer_callback(callback_id)
        return
    if data.startswith("product:"):
        product_id = int(data.split(":")[1])
        product = get_product(product_id)
        if not product or not product.is_active:
            answer_callback(callback_id, "Товар не найден")
            return
        show_product(chat_id, user_id, product)
        answer_callback(callback_id)
        return
    if data.startswith("fav:"):
        product_id = int(data.split(":")[1])
        added = toggle_favorite(user_id, product_id)
        answer_callback(callback_id, "Добавлено в избранное" if added else "Убрано из избранного")
        edit_reply_markup(chat_id, message_id, product_keyboard(product_id, user_id))
        return
    if user_id not in ADMIN_IDS:
        answer_callback(callback_id, "Нет доступа")
        return
    if data in {"admin:add", "admin:add:catalog", "admin:add:available"}:
        section = data.split(":")[2] if data.count(":") == 2 else "catalog"
        set_state(user_id, "add:title", {"section": section})
        place = "каталог" if section == "catalog" else "наличие"
        send_message(chat_id, f"Добавляем товар в раздел «{place}». Введите название товара:")
        answer_callback(callback_id)
    elif data == "admin:list":
        send_admin_products(chat_id, message_id)
        answer_callback(callback_id)
    elif data.startswith("admin:product:"):
        product_id = int(data.split(":")[2])
        product = get_product(product_id)
        if not product:
            answer_callback(callback_id, "Товар не найден")
            return
        edit_message_text(chat_id, message_id, product_text(product), admin_product_keyboard(product))
        answer_callback(callback_id)
    elif data.startswith("edit:"):
        _, product_id, field = data.split(":")
        labels = {
            "title": "новое название:",
            "price": "новую цену:",
            "sizes": "новые размеры:",
            "description": "новое описание:",
            "photo": "новое фото или -, чтобы удалить фото:",
        }
        set_state(user_id, "edit", {"product_id": int(product_id), "field": field})
        send_message(chat_id, f"Отправьте {labels[field]}")
        answer_callback(callback_id)
    elif data.startswith("toggle:"):
        product_id = int(data.split(":")[1])
        product = get_product(product_id)
        if not product:
            answer_callback(callback_id, "Товар не найден")
            return
        with closing(db()) as conn:
            conn.execute("UPDATE products SET is_active = ? WHERE id = ?", (0 if product.is_active else 1, product_id))
            conn.commit()
        updated = get_product(product_id)
        edit_message_text(chat_id, message_id, product_text(updated), admin_product_keyboard(updated))
        answer_callback(callback_id, "Готово")
    elif data.startswith("section:"):
        product_id = int(data.split(":")[1])
        product = get_product(product_id)
        if not product:
            answer_callback(callback_id, "Товар не найден")
            return
        new_section = "available" if product.section == "catalog" else "catalog"
        with closing(db()) as conn:
            conn.execute("UPDATE products SET section = ? WHERE id = ?", (new_section, product_id))
            conn.commit()
        updated = get_product(product_id)
        edit_message_text(chat_id, message_id, product_text(updated), admin_product_keyboard(updated))
        answer_callback(callback_id, "Раздел изменен")
    elif data.startswith("delete:"):
        _, product_id, action = data.split(":")
        product_id = int(product_id)
        if action == "ask":
            edit_message_text(
                chat_id,
                message_id,
                "Удалить товар навсегда?",
                inline_keyboard(
                    [
                        [{"text": "Да, удалить", "callback_data": f"delete:{product_id}:yes"}],
                        [{"text": "Отмена", "callback_data": f"admin:product:{product_id}"}],
                    ]
                ),
            )
            answer_callback(callback_id)
            return
        with closing(db()) as conn:
            conn.execute("DELETE FROM favorites WHERE product_id = ?", (product_id,))
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
        answer_callback(callback_id, "Товар удален")
        send_admin_products(chat_id, message_id)


def poll() -> None:
    offset = 0
    while True:
        try:
            allowed = json.dumps(["message", "callback_query"])
            result = request("getUpdates", {"offset": offset, "timeout": 30, "allowed_updates": allowed})
            for update in result["result"]:
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_message(update["message"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])
        except Exception:
            logging.exception("Polling error")
            time.sleep(3)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in .env")
    if not ADMIN_IDS:
        raise RuntimeError("Set ADMIN_IDS in .env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    me = request("getMe")["result"]
    logging.info("Bot started: @%s", me.get("username"))
    poll()


if __name__ == "__main__":
    main()
