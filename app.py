import os
import re
import json
import base64
import sqlite3
from datetime import datetime, timezone, timedelta, date

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
if _pw and not st.session_state.get("auth"):
    st.subheader("🔒 ورود")
    entered = st.text_input("رمز ورود", type="password")
    if st.button("ورود", type="primary"):
        if entered == _pw:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("رمز اشتباه است.")
    st.stop()


# ---------- دیتابیس تاریخچه ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, ad_name TEXT, views INTEGER, opens INTEGER,
        calls INTEGER, msgs INTEGER, price INTEGER,
        title TEXT, url TEXT)""")
    have = {r[1] for r in conn.execute("PRAGMA table_info(history)")}
    for col, typ in [("pub_date", "TEXT"), ("upd_date", "TEXT"),
                     ("days_active", "INTEGER")]:
        if col not in have:
            conn.execute(f"ALTER TABLE history ADD COLUMN {col} {typ}")
    return conn


def save_row(ad_name, views, opens, calls, msgs, price, title, url,
             pub_date="", upd_date="", days_active=0):
    conn = db()
    conn.execute(
        "INSERT INTO history (ts, ad_name, views, opens, calls, msgs, price, "
        "title, url, pub_date, upd_date, days_active) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (now_ir().strftime("%Y-%m-%d %H:%M"), ad_name, views, opens,
         calls, msgs, price, title, url, pub_date, upd_date, days_active))
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


# ---------- تبدیل عدد به حروف فارسی ----------
_ONES = ["", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه", "ده",
         "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده", "هفده",
         "هجده", "نوزده"]
_TENS = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
_HUNDREDS = ["", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد",
             "هشتصد", "نهصد"]
_SCALES = ["", "هزار", "میلیون", "میلیارد", "تریلیون"]


def _three(n):
    parts = []
    h, r = divmod(n, 100)
    if h:
        parts.append(_HUNDREDS[h])
    if r < 20:
        if r:
            parts.append(_ONES[r])
    else:
        tens, ones = divmod(r, 10)
        parts.append(_TENS[tens])
        if ones:
            parts.append(_ONES[ones])
    return " و ".join(parts)


def number_to_words(n):
    n = int(n)
    if n == 0:
        return "صفر"
    groups, i = [], 0
    while n and i < len(_SCALES):
        n, g = divmod(n, 1000)
        if g:
            groups.append((g, i))
        i += 1
    if n:
        return ""
    out = []
    for g, i in reversed(groups):
        w = _three(g)
        out.append(w + (" " + _SCALES[i] if _SCALES[i] else ""))
    return " و ".join(out)


def money_caption(n):
    if not n:
        return ""
    w = number_to_words(n)
    return f"{n:,} تومان" + (f" — {w} تومان" if w else "")


# ---------- تاریخ شمسی ----------
IR_TZ = timezone(timedelta(hours=3, minutes=30))


def now_ir():
    return datetime.now(IR_TZ)


def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100
            + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1])
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_to_gregorian(jy, jm, jd):
    if jy > 979:
        gy = 1600
        jy -= 979
    else:
        gy = 621
    days = (365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + 78 + jd
            + ((jm - 1) * 31 if jm < 8 else ((jm - 7) * 30 + 186)))
    gy += 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    sal_a = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def date_to_jalali_str(d):
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def parse_jalali(s):
    """تاریخ شمسی مثل 1405/08/10 را به date میلادی تبدیل می‌کند؛ نامعتبر = None."""
    if not s or not s.strip():
        return None
    s = s.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                                          "01234567890123456789"))
    parts = re.split(r"[/\-.]", s)
    if len(parts) != 3:
        return None
    try:
        jy, jm, jd = (int(p) for p in parts)
    except ValueError:
        return None
    if not (1300 <= jy <= 1500 and 1 <= jm <= 12 and 1 <= jd <= 31):
        return None
    try:
        gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
        d = date(gy, gm, gd)
    except Exception:
        return None
    if gregorian_to_jalali(d.year, d.month, d.day) != (jy, jm, jd):
        return None
    return d


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
def price_issues(price, market):
    out = []
    if price and market:
        diff = (price - market) / market
        if diff > 0.10:
            out.append(f"قیمت شما {diff:.0%} بالاتر از بازار است.")
        elif diff < -0.15:
            out.append(f"قیمت شما {-diff:.0%} پایین‌تر از بازار است؛ ممکن است مشکوک به نظر برسد.")
    return out


def analyze(views, opens, calls, msgs, price, market,
            min_open, min_contact, min_views, days=1, min_daily=20):
    if views < min_views:
        return ["بازدید هنوز برای قضاوت کافی نیست. چند روز صبر کن یا آگهی را نردبان کن."]
    issues = []
    open_rate, contact_rate = rates(views, opens, calls, msgs)

    daily = views / max(days, 1)
    if daily < min_daily:
        issues.append(
            f"بازدید روزانه کم است (حدود {daily:.0f} در روز): آگهی کم دیده می‌شود. "
            "دسته‌بندی، محله، عنوان و نردبان کردن را بررسی کن.")
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
    issues += price_issues(price, market)
    if not issues:
        issues.append("آمار در محدوده‌ی عادی است. زمان بیشتری بده یا آگهی را نردبان کن.")
    return issues


# ---------- بررسی عنوان، توضیحات و عکس با هوش مصنوعی ----------
def build_prompt(stats, price, market, title, desc, issues, images,
                 published=True, timing=""):
    market_txt = (market if market else
                  "نامشخص (آگهی مشابه پیدا نشد؛ درباره‌ی قیمت نظر نده)")
    img_txt = ("عکس‌های آگهی هم ضمیمه شده‌اند." if images
               else "عکسی ضمیمه نشده.")
    notes = "\n".join(issues)

    if published:
        context = f"""وضعیت: آگهی منتشر شده است.
زمان‌بندی: {timing}
آمار آگهی: {stats}
مشکلات تشخیص‌داده‌شده از روی آمار:
{notes}"""
        tasks = """۱. عنوان فعلی را نقد کن (وضوح، کلمات کلیدی، جذابیت).
۲. توضیحات فعلی را نقد کن.
۳. درباره‌ی قیمت نسبت به بازار نظر بده (اگر قیمت بازار داده شده).
۴. اگر عکس هست، کیفیت و نقاط ضعف عکس‌ها را بگو (نور، زاویه، شلوغی، عکس اول).
۵. با توجه به تاریخ انتشار، تعداد روزها و تاریخ آخرین بروزرسانی بگو آمار چقدر قابل اتکاست، و اگر بروزرسانی بوده، آیا نشانه‌ای از بهتر شدن روند هست یا داده کافی برای قضاوت نیست (صریح بگو). پیشنهاد کلی برای زمان بروزرسانی بده و ادعای قطعی درباره‌ی الگوریتم دیوار نکن.
۶. ۵ عنوان پیشنهادی بده، هرکدام حداکثر ۷۰ کاراکتر.
۷. یک توضیحات کامل پیشنهادی بنویس.
۸. چند نمونه آگهی کامل با تکنیک‌های تبلیغاتی بنویس: ۲ نمونه با AIDA (توجه ← علاقه ← تمایل ← اقدام) و ۲ نمونه با PAS (مشکل ← تشدید مشکل ← راه‌حل). هر نمونه عنوان (حداکثر ۷۰ کاراکتر) و متن کامل داشته باشد و در یک جمله بگو هر بخش تکنیک کجای متن است."""
        keys = '''{{"title_review": "", "description_review": "", "price_review": "",
"image_review": "", "timing_review": "", "suggested_titles": ["","","","",""],
"suggested_description": "",
"aida_samples": [{{"title":"","body":"","note":""}},{{"title":"","body":"","note":""}}],
"pas_samples": [{{"title":"","body":"","note":""}},{{"title":"","body":"","note":""}}]}}'''
    else:
        context = f"""وضعیت: آگهی هنوز منتشر نشده است. کاربر می‌خواهد قبل از انتشار بهترین نسخه‌ی ممکن را بسازد. آمار وجود ندارد.
نکته‌ی قیمت از مقایسه‌ی ساده: {notes}"""
        tasks = """۱. عنوان فعلی را نقد کن (اگر نوشته شده)؛ وضوح، کلمات کلیدی، جذابیت.
۲. توضیحات فعلی را نقد کن.
۳. درباره‌ی قیمت نسبت به بازار نظر بده (اگر قیمت بازار داده شده).
۴. اگر عکس هست، کیفیت و نقاط ضعف عکس‌ها را بگو.
۵. یک چک‌لیست کوتاه از کارهایی که قبل از انتشار باید انجام شود (اطلاعات ناقص، عکس‌ها، قیمت، جزئیات اعتمادساز).
۶. ۵ عنوان پیشنهادی بده، هرکدام حداکثر ۷۰ کاراکتر.
۷. بهترین توضیحات ممکن را برای انتشار بنویس.
۸. چند نمونه آگهی کامل با تکنیک‌های تبلیغاتی بنویس: ۲ نمونه با AIDA (توجه ← علاقه ← تمایل ← اقدام) و ۲ نمونه با PAS (مشکل ← تشدید مشکل ← راه‌حل). هر نمونه عنوان (حداکثر ۷۰ کاراکتر) و متن کامل داشته باشد و در یک جمله بگو هر بخش تکنیک کجای متن است."""
        keys = '''{{"title_review": "", "description_review": "", "price_review": "",
"image_review": "", "prepublish_checklist": ["",""],
"suggested_titles": ["","","","",""], "suggested_description": "",
"aida_samples": [{{"title":"","body":"","note":""}},{{"title":"","body":"","note":""}}],
"pas_samples": [{{"title":"","body":"","note":""}},{{"title":"","body":"","note":""}}]}}'''

    prompt = f"""تو کارشناس آگهی‌های مالکی در دیوار هستی.

{context}
قیمت آگهی: {price} | قیمت مشابه‌های بازار: {market_txt}

عنوان فعلی: {title}

توضیحات فعلی:
{desc}

{img_txt}

وظیفه:
{tasks}

قانون مهم: در همه‌ی عنوان‌ها، توضیحات و نمونه‌های پیشنهادی هیچ ویژگی یا اطلاعاتی که در متن فعلی نیامده اختراع نکن و ادعای اغراق‌آمیز یا دروغ ننویس. اگر اطلاعاتی لازم است، جایش را به‌صورت [اینجا پر شود] بگذار.

""" + "فقط یک JSON معتبر برگردان، بدون هیچ متن اضافه و بدون ```، با این کلیدها:\n" + keys.format() + "\nاگر بخشی قابل ارزیابی نبود، رشته‌ی خالی بگذار. همه‌چیز به فارسی."
    return prompt


def parse_json(raw):
    clean = re.sub(r"```json|```", "", raw).strip()
    try:
        return json.loads(clean)
    except Exception:
        return {"raw": raw}


def ai_review_claude(prompt, images):
    import anthropic
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    content = []
    for img in images:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": img.type,
            "data": base64.b64encode(img.getvalue()).decode()}})
    content.append({"type": "text", "text": prompt})
    resp = client.messages.create(
        model=MODEL, max_tokens=3000,
        messages=[{"role": "user", "content": content}])
    return parse_json(resp.content[0].text)


def ai_review_gemini(prompt, images):
    model = secret("GEMINI_MODEL") or "gemini-flash-latest"
    parts = [{"inline_data": {
        "mime_type": img.type,
        "data": base64.b64encode(img.getvalue()).decode()}} for img in images]
    parts.append({"text": prompt})
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": secret("GEMINI_API_KEY"),
                 "Content-Type": "application/json"},
        json={"contents": [{"parts": parts}],
              "generationConfig": {"responseMimeType": "application/json"}},
        timeout=90)
    r.raise_for_status()
    out = r.json()["candidates"][0]["content"]["parts"]
    raw = "".join(p.get("text", "") for p in out)
    return parse_json(raw)


def ai_review(prompt, images):
    if secret("GEMINI_API_KEY"):
        return ai_review_gemini(prompt, images)
    return ai_review_claude(prompt, images)


# ---------- نوار کناری ----------
with st.sidebar:
    st.header("حد معمول (قابل تغییر)")
    min_open = st.number_input("حداقل نرخ باز شدن (%)", value=4.0) / 100
    min_contact = st.number_input("حداقل نرخ تماس+پیام (%)", value=5.0) / 100
    min_views = st.number_input("حداقل بازدید برای قضاوت", value=200)
    min_daily = st.number_input("حداقل بازدید روزانه", value=20)

    _k = secret("GEMINI_API_KEY") or secret("ANTHROPIC_API_KEY")
    st.caption("🔑 کلید: پیدا نشد" if not _k else
               f"🔑 شروع کلید: {_k[:4]} | طول: {len(_k)}")

def show_samples(label, items):
    if not items:
        return
    st.subheader(label)
    for n, it in enumerate(items, 1):
        if isinstance(it, dict):
            st.markdown(f"**نمونه {n}: {it.get('title', '')}**")
            st.text_area("قابل کپی", it.get("body", ""), height=200,
                         key=f"{label}_{n}")
            if it.get("note"):
                st.caption(it["note"])
        else:
            st.write(it)


def show_result(res):
    if "raw" in res:
        st.write(res["raw"])
        return
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
        st.text_area("قابل کپی", res["suggested_description"], height=250)
    if res.get("price_review"):
        st.subheader("نظر درباره‌ی قیمت")
        st.write(res["price_review"])
    if res.get("image_review"):
        st.subheader("بررسی عکس‌ها")
        st.write(res["image_review"])
    if res.get("timing_review"):
        st.subheader("تحلیل زمان‌بندی")
        st.write(res["timing_review"])
    chk = res.get("prepublish_checklist")
    if chk:
        st.subheader("چک‌لیست قبل از انتشار")
        if isinstance(chk, list):
            for c in chk:
                st.write("• " + str(c))
        else:
            st.write(chk)
    show_samples("نمونه‌ها با تکنیک AIDA", res.get("aida_samples"))
    show_samples("نمونه‌ها با تکنیک PAS", res.get("pas_samples"))


def analysis_ui(published, ad_name, title, desc, views, opens, calls, msgs,
                price, market, images, save_it, pub_date, upd_date,
                pub_j, upd_j, basis, today):
    timing = ""
    days_eff = 0
    if published:
        days_pub = (today - pub_date).days
        days_upd = (today - upd_date).days if upd_date else None
        use_upd = basis == "از آخرین بروزرسانی" and upd_date is not None
        days_eff = max(days_upd if use_upd else days_pub, 1)
        upd_txt = f"{upd_j} ({days_upd} روز پیش)" if upd_date else "ندارد"
        timing = (f"تاریخ انتشار: {pub_j} ({days_pub} روز پیش). "
                  f"آخرین بروزرسانی/نردبان: {upd_txt}. "
                  f"آمار از {basis} تا امروز حساب شده ({days_eff} روز). "
                  f"میانگین روزانه: بازدید {views / days_eff:.1f}، "
                  f"باز شدن {opens / days_eff:.1f}، "
                  f"تماس+پیام {(calls + msgs) / days_eff:.1f}.")
        issues = analyze(views, opens, calls, msgs, price, market,
                         min_open, min_contact, min_views, days_eff, min_daily)
        open_rate, contact_rate = rates(views, opens, calls, msgs)

        st.subheader("خلاصه‌ی زمانی")
        st.write(timing)
        e1, e2, e3 = st.columns(3)
        e1.metric("بازدید روزانه", f"{views / days_eff:.1f}")
        e2.metric("باز شدن روزانه", f"{opens / days_eff:.1f}")
        e3.metric("تماس+پیام روزانه", f"{(calls + msgs) / days_eff:.1f}")

        st.subheader("قیف آگهی")
        funnel = pd.DataFrame({"تعداد": [views, opens, calls + msgs]},
                              index=["بازدید", "باز شدن", "تماس + پیام"])
        st.bar_chart(funnel)
        m1, m2 = st.columns(2)
        m1.metric("نرخ باز شدن", f"{open_rate:.1%}")
        m2.metric("نرخ تماس+پیام", f"{contact_rate:.1%}")

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
                         title, st.session_state.url, pub_j, upd_j, days_eff)
                st.success("در تاریخچه ذخیره شد.")
            else:
                st.info("برای ذخیره در تاریخچه، یک اسم برای آگهی بنویس.")
        stats_txt = (f"بازدید {views}، باز شدن {opens}، تماس {calls}، "
                     f"پیام {msgs}")
    else:
        issues = price_issues(price, market) or ["مشکل مشخصی در مقایسه‌ی قیمت دیده نشد."]
        st.subheader("بررسی اولیه")
        for i in issues:
            st.info(i)
        stats_txt = "آگهی هنوز منتشر نشده؛ آمار ندارد."

    # پرامپت آماده برای کپی در هر چت‌بات (بدون نیاز به API)
    _p = build_prompt(stats_txt, price, market, title, desc, issues, [],
                      published, timing)
    manual = (_p.split("فقط یک JSON معتبر برگردان")[0]
              + "پاسخ را با عنوان‌های مرتب و خوانا به فارسی بنویس. "
                "اگر عکس‌هایی همراه این پیام فرستادم، آن‌ها را هم بررسی کن.")
    has_key = bool(secret("GEMINI_API_KEY") or secret("ANTHROPIC_API_KEY"))
    with st.expander("📋 پرامپت آماده (کپی در Claude یا هر چت‌بات)",
                     expanded=not has_key):
        st.code(manual, language=None)
        st.caption("با دکمه‌ی کپی بالا سمت راست کادر کپی کن. "
                   "اگر عکس داری، خودت داخل چت‌بات هم آپلود کن.")

    if title.strip() or desc.strip():
        if not has_key:
            st.info("کلید API تنظیم نشده؛ از پرامپت آماده‌ی بالا استفاده کن.")
            return
        prompt = build_prompt(stats_txt, price, market, title, desc, issues,
                              images[:3], published, timing)
        with st.spinner("در حال بررسی با هوش مصنوعی..."):
            try:
                res = ai_review(prompt, images[:3])
            except Exception as e:
                res = None
                detail = ""
                resp_ = getattr(e, "response", None)
                if resp_ is not None:
                    detail = f" | کد {resp_.status_code}: {resp_.text[:300]}"
                st.error(f"خطا در ارتباط با هوش مصنوعی: {type(e).__name__}{detail}")
        if res:
            show_result(res)


tab1, tab2 = st.tabs(["🔍 تحلیل جدید", "📈 تاریخچه و روند"])

# =====================================================
with tab1:
    status = st.radio("وضعیت آگهی", ["هنوز منتشر نکرده‌ام", "منتشر کرده‌ام"],
                      horizontal=True, key="status")
    published = status == "منتشر کرده‌ام"
    if not published:
        st.info("آگهی را قبل از انتشار بررسی می‌کنیم تا بهترین نسخه‌ی ممکن را "
                "منتشر کنی. آمار لازم نیست.")

    st.text_input("لینک آگهی (اختیاری)", key="url",
                  placeholder="https://divar.ir/v/...")
    st.button("دریافت عنوان و توضیحات از لینک", on_click=do_fetch)
    msg = st.session_state.fetch_msg
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])

    ad_name = st.text_input("اسم دلخواه آگهی (برای تاریخچه)")
    title = st.text_input("عنوان آگهی", key="title")
    desc = st.text_area("توضیحات آگهی", key="desc", height=180)

    today = now_ir().date()
    views = opens = calls = msgs = 0
    pub_j = upd_j = ""
    pub_date = upd_date = None
    basis = "از تاریخ انتشار"
    date_error = None

    if published:
        st.caption(f"امروز: {date_to_jalali_str(today)}")
        da, dbb = st.columns(2)
        pub_j = da.text_input("تاریخ انتشار (شمسی)", key="pub_j",
                              placeholder="مثلاً 1405/08/10")
        upd_j = dbb.text_input("تاریخ آخرین بروزرسانی (اختیاری)", key="upd_j",
                               placeholder="اگر نکردی خالی بگذار")
        basis = st.radio("آمار از چه زمانی حساب شده؟",
                         ["از تاریخ انتشار", "از آخرین بروزرسانی"],
                         horizontal=True)
        pub_date = parse_jalali(pub_j)
        upd_date = parse_jalali(upd_j)
        if pub_date is None:
            date_error = "تاریخ انتشار را درست وارد کن، مثل 1405/08/10"
        elif pub_date > today:
            date_error = "تاریخ انتشار نمی‌تواند در آینده باشد."
        elif upd_j.strip() and upd_date is None:
            date_error = "تاریخ بروزرسانی را درست وارد کن، مثل 1405/08/12"
        elif upd_date and (upd_date < pub_date or upd_date > today):
            date_error = "تاریخ بروزرسانی باید بین تاریخ انتشار و امروز باشد."
        elif basis == "از آخرین بروزرسانی" and upd_date is None:
            date_error = "برای این گزینه، تاریخ بروزرسانی را وارد کن."
        if pub_date and not date_error:
            st.caption(f"{(today - pub_date).days} روز از انتشار گذشته.")

        c1, c2 = st.columns(2)
        views = c1.number_input("بازدید آگهی", min_value=0, step=10)
        opens = c2.number_input("باز شدن آگهی", min_value=0, step=1)
        calls = c1.number_input("تماس", min_value=0, step=1)
        msgs = c2.number_input("پیام", min_value=0, step=1)

    p1, p2 = st.columns(2)
    price = p1.number_input("قیمت آگهی شما (تومان)", min_value=0,
                            step=1000000, key="price")
    p1.caption(money_caption(price))
    market = p2.number_input("قیمت مشابه‌های بازار (تومان)", min_value=0,
                             step=1000000,
                             disabled=st.session_state.get("no_market", False))
    no_market = p2.checkbox("آگهی مشابه ندیدم", key="no_market")
    if no_market:
        market = 0
    else:
        p2.caption(money_caption(market))

    images = st.file_uploader("عکس‌های آگهی (حداکثر ۳ تا، اختیاری)",
                              type=["jpg", "jpeg", "png", "webp"],
                              accept_multiple_files=True)
    save_it = (st.checkbox("بعد از تحلیل در تاریخچه ذخیره شود", value=True)
               if published else False)

    if st.button("تحلیل کن" if published else "بررسی و بهبود آگهی",
                 type="primary"):
        if date_error:
            st.error(date_error)
        else:
            analysis_ui(published, ad_name, title, desc, views, opens, calls,
                        msgs, price, market, images, save_it, pub_date,
                        upd_date, pub_j, upd_j, basis, today)

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
