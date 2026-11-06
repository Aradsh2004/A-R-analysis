import os
import streamlit as st

st.set_page_config(page_title="تحلیل آگهی دیوار", page_icon="📊")
st.markdown("<style>body,.stApp{direction:rtl;text-align:right}</style>",
            unsafe_allow_html=True)
st.title("📊 تحلیل آمار آگهی دیوار")


def secret(name):
    try:
        return st.secrets[name]
    except Exception:
        return os.getenv(name)


# قفل با رمز (اگر APP_PASSWORD تنظیم شده باشد)
_pw = secret("APP_PASSWORD")
if _pw and st.text_input("رمز ورود", type="password") != _pw:
    st.stop()

# ---------- ورودی‌ها ----------
with st.sidebar:
    st.header("حد معمول (قابل تغییر)")
    min_open = st.number_input("حداقل نرخ باز شدن (%)", value=4.0) / 100
    min_contact = st.number_input("حداقل نرخ تماس+پیام (%)", value=5.0) / 100
    min_views = st.number_input("حداقل بازدید برای قضاوت", value=200)

c1, c2 = st.columns(2)
views = c1.number_input("بازدید آگهی", min_value=0, step=10)
opens = c2.number_input("باز شدن آگهی", min_value=0, step=1)
calls = c1.number_input("تماس", min_value=0, step=1)
msgs = c2.number_input("پیام", min_value=0, step=1)

p1, p2 = st.columns(2)
price = p1.number_input("قیمت آگهی شما (تومان)", min_value=0, step=1000000)
market = p2.number_input("قیمت مشابه‌های بازار (تومان)", min_value=0,
                         step=1000000)

text = st.text_area("متن آگهی", height=200)


# ---------- تحلیل آمار (قانون‌محور) ----------
def analyze():
    issues = []
    if views < min_views:
        return ["بازدید هنوز برای قضاوت کافی نیست. چند روز صبر کن یا آگهی را نردبان کن."]

    open_rate = opens / views
    contact_rate = (calls + msgs) / opens if opens else 0

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
        issues.append("آمار در محدوده‌ی عادی است. بهتر است زمان بیشتری بدهی یا آگهی را نردبان کنی.")
    return issues


# ---------- تحلیل متن با هوش مصنوعی ----------
def analyze_text(issues):
    import anthropic
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    prompt = f"""تو کارشناس آگهی‌های مالکی در دیوار هستی.
آمار آگهی: بازدید {views}، باز شدن {opens}، تماس {calls}، پیام {msgs}
قیمت آگهی: {price} | قیمت بازار: {market}
مشکلات تشخیص‌داده‌شده از آمار:
{chr(10).join(issues)}

متن آگهی:
{text}

با توجه به مشکلات بالا، نقاط ضعف متن را مشخص کن، سپس یک عنوان و یک متن
بهبودیافته پیشنهاد بده. کوتاه و کاربردی و به فارسی بنویس."""
    resp = client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


if st.button("تحلیل کن", type="primary"):
    issues = analyze()
    st.subheader("تشخیص از روی آمار")
    for i in issues:
        st.warning(i)

    if text.strip():
        if not secret("ANTHROPIC_API_KEY"):
            st.error("کلید API تنظیم نشده (ANTHROPIC_API_KEY).")
        else:
            with st.spinner("در حال بررسی متن آگهی..."):
                st.subheader("بررسی متن و پیشنهاد")
                st.write(analyze_text(issues))
