import os
import re
import json
import base64
import sqlite3
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

MODEL = "claude-sonnet-5-5"
DB_PATH = "history.db"

st.set_page_config(page_title="تحلیل آگهی دیوار", page_icon="📊")
st.markdown("<style>body,.stApp{direction:rtl;text-align:right}</style>",
            unsafe_allow_html=True)
st.title("📊 تحلیل آگهی دیوار")


def secret(name):
    try:
        val = st.secrets[name]
    except Exception:
        val = os.getenv(name)
    return val.strip() if isinstance(val, str) else val


# ---------- قفل با رمز ----------
_pw = secret("APP_PASSWORD")
if _pw and st.text_input("رمز ورود", type="password") != _pw:
    st.stop()


# ---------- دیتابیس تاریخچه ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, ad_name TEXT, views INTEGER, opens INTEGER,
        calls INTEGER, msgs INTEGER, price INTEGER,
        title TEXT, url TEXT)""")
    return conn


def save_row(ad_name, views, opens, calls, msgs, price, title, url):
    conn = db()
    conn.execute(
        "INSERT INTO history (ts, ad_name, views, opens, calls, msgs, price, title, url) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M"), ad_name, views, opens,
         calls, msgs, price, title, url))
    conn.commit()
    conn.close()


def load_df():
    conn = db()
    df = pd.read_sql_query("SELECT * FROM history ORDER BY ts", conn)
    conn.close()
    return df


def last_row(ad_name):
    df = load_df()
    df = df[df["ad_name"] == ad_name]
    return None if df.empty else df.iloc[-1]


# ---------- محاسبه نرخ‌ها ----------
def rates(views, opens, calls, msgs):
    open_rate = opens / views if views else 0
    contact_rate = (calls + msgs) / opens if opens else 0
    return open_rate, contact_rate


# ---------- دریافت آگهی از لینک ----------
def fetch_ad(url):
    headers = {
        "User-Agent": ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"),
        "Accept-Language": "fa-IR,fa;q=0.9",
    }
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    title, desc, price = "", "", 0
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        for d in (data if isinstance(data, list) else [data]):
            if not isinstance(d, dict):
                continue
            title = title or str(d.get("name", ""))
            desc = desc or str(d.get("description", ""))
            offers = d.get("offers")
            if isinstance(offers, dict) and not price:
                try:
                    price = int(float(offers.get("price", 0)))
                except Exception:
                    pass

    def meta(prop):
        tag = (soup.find("meta", attrs={"property": prop})
               or soup.find("meta", attrs={"name": prop}))
        return tag["content"].strip() if tag and tag.get("content") else ""

    title = title or meta("og:title")
    desc = desc or meta("og:description") or meta("description")
    title = re.sub(r"\s*[|\-–]\s*دیوار.*$", "", title).strip()
    if not title and not desc:
        raise ValueError("عنوان و توضیحات در صفحه پیدا نشد.")
    return title, desc, price


for k, v in {"url": "", "title": "", "desc": "", "price": 0,
             "fetch_msg": None}.items():
    st.session_state.setdefault(k, v)


def do_fetch():
    try:
        t, d, p = fetch_ad(st.session_state.url.strip())
        st.session_state.title = t
        st.session_state.desc = d
        if p:
            st.session_state.price = p
        st.session_state.fetch_msg = (
            "ok", "دریافت شد. لطفاً متن و قیمت را یک بار چک کن.")
    except Exception as e:
        st.session_state.fetch_msg = (
            "err", f"دریافت از لینک نشد ({type(e).__name__}). "
                   "عنوان و توضیحات را دستی وارد کن.")


# ---------- تحلیل آمار (قانون‌محور) ----------
def analyze(views, opens, calls, msgs, price, market,
            min_open, min_contact, min_views):
    if views < min_views:
        return ["بازدید هنوز برای قضاوت کافی نیست. چند روز صبر کن یا آگهی را نردبان کن."]
    issues = []
    open_rate, contact_rate = rates(views, opens, calls, msgs)

    if open_rate < min_open:
        issues.append(
            f"نرخ باز شدن پایین است ({open_rate:.1%}): "
            "مشکل احتمالاً در عنوان، عکس اول یا قیمتی است که در لیست دیده می‌شود.")
    if opens and contact_rate < min_contact:
        issues.append(
            f"نرخ تماس/پیام پایین است ({contact_rate:.1%}): "
            "مردم آگهی را باز می‌کنند ولی اقدام نمی‌کنند. متن، توضیحات، "
            "عکس‌های داخل آگهی یا قیمت را بررسی کن.")
    if opens and calls and not msgs:
        issues.append("پیام صفر است؛ شاید چت فعال نیست یا مخاطب فقط تماس را ترجیح می‌دهد.")
    if opens and msgs and not calls:
        issues.append("تماس صفر است؛ شاید شماره‌ات پنهان است یا ساعت پاسخگویی مشخص نیست.")
    if price and market:
        diff = (price - market) / market
        if diff > 0.10:
            issues.append(f"قیمت شما {diff:.0%} بالاتر از بازار است.")
        elif diff < -0.15:
            issues.append(f"قیمت شما {-diff:.0%} پایین‌تر از بازار است؛ ممکن است مشکوک به نظر برسد.")
    if not issues:
        issues.append("آمار در محدوده‌ی عادی است. زمان بیشتری بده یا آگهی را نردبان کن.")
    return issues


# ---------- بررسی عنوان، توضیحات و عکس با هوش مصنوعی ----------
def ai_review(stats, price, market, title, desc, issues, images):
    import anthropic
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))

    content = []
    for img in images:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": img.type,
            "data": base64.b64encode(img.getvalue()).decode()}})

    prompt = f"""تو کارشناس آگهی‌های مالکی در دیوار هستی.

آمار آگهی: {stats}
قیمت آگهی: {price} | قیمت مشابه‌های بازار: {market}
مشکلات تشخیص‌داده‌شده از روی آمار:
{chr(10).join(issues)}

عنوان فعلی: {title}

توضیحات فعلی:
{desc}

{"عکس‌های آگهی هم ضمیمه شده‌اند." if images else "عکسی ضمیمه نشده."}

وظیفه:
۱. عنوان فعلی را نقد کن (وضوح، کلمات کلیدی، جذابیت).
۲. توضیحات فعلی را نقد کن.
۳. درباره‌ی قیمت نسبت به بازار نظر بده (اگر قیمت بازار داده شده).
۴. اگر عکس هست، کیفیت و نقاط ضعف عکس‌ها را بگو (نور، زاویه، شلوغی، عکس اول).
۵. ۵ عنوان پیشنهادی بده، هرکدام حداکثر ۷۰ کاراکتر.
۶. یک توضیحات کامل پیشنهادی بنویس.

قانون مهم: در عنوان و توضیحات پیشنهادی هیچ ویژگی یا اطلاعاتی که در متن فعلی
نیامده اختراع نکن. اگر اطلاعاتی لازم است، جایش را به‌صورت [اینجا پر شود] بگذار.

فقط یک JSON معتبر برگردان، بدون هیچ متن اضافه و بدون ```، با این کلیدها:
{{"title_review": "", "description_review": "", "price_review": "",
"image_review": "", "suggested_titles": ["","","","",""],
"suggested_description": ""}}
اگر بخشی قابل ارزیابی نبود، رشته‌ی خالی بگذار. همه‌چیز به فارسی."""
    content.append({"type": "text", "text": prompt})

    resp = client.messages.create(
        model=MODEL, max_tokens=3000,
        messages=[{"role": "user", "content": content}])
    raw = resp.content[0].text
    clean = re.sub(r"```json|```", "", raw).strip()
    try:
        return json.loads(clean)
    except Exception:
        return {"raw": raw}


# ---------- نوار کناری ----------
with st.sidebar:
    st.header("حد معمول (قابل تغییر)")
    min_open = st.number_input("حداقل نرخ باز شدن (%)", value=4.0) / 100
    min_contact = st.number_input("حداقل نرخ تماس+پیام (%)", value=5.0) / 100
    min_views = st.number_input("حداقل بازدید برای قضاوت", value=200)

    _k = secret("ANTHROPIC_API_KEY")
    st.caption("🔑 کلید: پیدا نشد" if not _k else
               f"🔑 شروع کلید: {_k[:7]} | طول: {len(_k)}")

tab1, tab2 = st.tabs(["🔍 تحلیل جدید", "📈 تاریخچه و روند"])

# =====================================================
with tab1:
    st.text_input("لینک آگهی (اختیاری)", key="url",
                  placeholder="https://divar.ir/v/...")
    st.button("دریافت عنوان و توضیحات از لینک", on_click=do_fetch)
    msg = st.session_state.fetch_msg
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])

    ad_name = st.text_input("اسم دلخواه آگهی (برای تاریخچه)")
    title = st.text_input("عنوان آگهی", key="title")
    desc = st.text_area("توضیحات آگهی", key="desc", height=180)

    c1, c2 = st.columns(2)
    views = c1.number_input("بازدید آگهی", min_value=0, step=10)
    opens = c2.number_input("باز شدن آگهی", min_value=0, step=1)
    calls = c1.number_input("تماس", min_value=0, step=1)
    msgs = c2.number_input("پیام", min_value=0, step=1)

    p1, p2 = st.columns(2)
    price = p1.number_input("قیمت آگهی شما (تومان)", min_value=0,
                            step=1000000, key="price")
    market = p2.number_input("قیمت مشابه‌های بازار (تومان)", min_value=0,
                             step=1000000)

    images = st.file_uploader("عکس‌های آگهی (حداکثر ۳ تا، اختیاری)",
                              type=["jpg", "jpeg", "png", "webp"],
                              accept_multiple_files=True)
    save_it = st.checkbox("بعد از تحلیل در تاریخچه ذخیره شود", value=True)

    if st.button("تحلیل کن", type="primary"):
        issues = analyze(views, opens, calls, msgs, price, market,
                         min_open, min_contact, min_views)
        open_rate, contact_rate = rates(views, opens, calls, msgs)

        st.subheader("قیف آگهی")
        funnel = pd.DataFrame({
            "تعداد": [views, opens, calls + msgs]},
            index=["بازدید", "باز شدن", "تماس + پیام"])
        st.bar_chart(funnel)
        m1, m2 = st.columns(2)
        m1.metric("نرخ باز شدن", f"{open_rate:.1%}")
        m2.metric("نرخ تماس+پیام", f"{contact_rate:.1%}")

        # مقایسه با ثبت قبلی همین آگهی
        if ad_name.strip():
            prev = last_row(ad_name.strip())
            if prev is not None:
                st.subheader("مقایسه با ثبت قبلی")
                p_or, p_cr = rates(prev["views"], prev["opens"],
                                   prev["calls"], prev["msgs"])
                d1, d2 = st.columns(2)
                d1.metric("نرخ باز شدن", f"{open_rate:.1%}",
                          f"{(open_rate - p_or) * 100:+.1f}٪")
                d2.metric("نرخ تماس+پیام", f"{contact_rate:.1%}",
                          f"{(contact_rate - p_cr) * 100:+.1f}٪")
                changes = []
                if prev["title"] != title:
                    changes.append("عنوان عوض شده")
                if prev["price"] != price:
                    changes.append(f"قیمت از {prev['price']:,} به {price:,}")
                if changes:
                    st.info("تغییرات نسبت به ثبت قبلی: " + "، ".join(changes))
                st.caption("توجه: مقایسه وقتی معنی‌دار است که بازدید هر دو ثبت کافی باشد.")

        st.subheader("تشخیص از روی آمار")
        for i in issues:
            st.warning(i)

        if save_it:
            if ad_name.strip():
                save_row(ad_name.strip(), views, opens, calls, msgs, price,
                         title, st.session_state.url)
                st.success("در تاریخچه ذخیره شد.")
            else:
                st.info("برای ذخیره در تاریخچه، یک اسم برای آگهی بنویس.")

        if title.strip() or desc.strip():
            if not secret("ANTHROPIC_API_KEY"):
                st.error("کلید API تنظیم نشده (ANTHROPIC_API_KEY).")
            else:
                stats = (f"بازدید {views}، باز شدن {opens}، تماس {calls}، "
                         f"پیام {msgs}")
                with st.spinner("در حال بررسی با هوش مصنوعی..."):
                    try:
                        res = ai_review(stats, price, market, title, desc,
                                        issues, images[:3])
                    except Exception as e:
                        res = None
                        st.error(f"خطا در ارتباط با هوش مصنوعی: {type(e).__name__}")
                if res:
                    if "raw" in res:
                        st.write(res["raw"])
                    else:
                        if res.get("title_review"):
                            st.subheader("نقد عنوان")
                            st.write(res["title_review"])
                        if res.get("suggested_titles"):
                            st.subheader("عنوان‌های پیشنهادی")
                            for t in res["suggested_titles"]:
                                st.code(t, language=None)
                        if res.get("description_review"):
                            st.subheader("نقد توضیحات")
                            st.write(res["description_review"])
                        if res.get("suggested_description"):
                            st.subheader("توضیحات پیشنهادی")
                            st.text_area("قابل کپی", res["suggested_description"],
                                         height=250)
                        if res.get("price_review"):
                            st.subheader("نظر درباره‌ی قیمت")
                            st.write(res["price_review"])
                        if res.get("image_review"):
                            st.subheader("بررسی عکس‌ها")
                            st.write(res["image_review"])

# =====================================================
with tab2:
    st.caption("توجه: روی Streamlit Cloud ممکن است تاریخچه با ری‌استارت اپ پاک شود. "
               "از دکمه‌ی دانلود پشتیبان استفاده کن.")
    df = load_df()
    if df.empty:
        st.info("هنوز چیزی ذخیره نشده.")
    else:
        name = st.selectbox("آگهی", sorted(df["ad_name"].unique()))
        sub = df[df["ad_name"] == name].copy()
        sub["نرخ باز شدن %"] = (sub["opens"] / sub["views"].where(sub["views"] > 0) * 100).round(1)
        sub["نرخ تماس+پیام %"] = ((sub["calls"] + sub["msgs"])
                                  / sub["opens"].where(sub["opens"] > 0) * 100).round(1)
        if len(sub) > 1:
            st.line_chart(sub.set_index("ts")[["نرخ باز شدن %", "نرخ تماس+پیام %"]])
        st.dataframe(sub.drop(columns=["id"]), use_container_width=True)

    if not df.empty:
        st.download_button("دانلود پشتیبان (CSV)",
                           df.to_csv(index=False).encode("utf-8-sig"),
                           file_name="history_backup.csv", mime="text/csv")

    up = st.file_uploader("بازگردانی از فایل پشتیبان", type=["csv"], key="restore")
    if up is not None and st.button("بازگردانی"):
        try:
            back = pd.read_csv(up).drop(columns=["id"], errors="ignore")
            conn = db()
            back.to_sql("history", conn, if_exists="append", index=False)
            conn.close()
            st.success("بازگردانی شد. صفحه را رفرش کن.")
        except Exception as e:
            st.error(f"بازگردانی نشد: {type(e).__name__}")
