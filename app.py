from flask import Flask, render_template, request, redirect, url_for, make_response
from datetime import datetime
import anthropic
import json
import os
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
USE_SUPABASE = False
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        USE_SUPABASE = True
        print("Supabase connected!")
    except Exception as e:
        print(f"Supabase not available: {e}")
        USE_SUPABASE = False
else:
    print("Supabase env vars missing — running in local JSON mode")

app = Flask(__name__)
DATA_FILE = "budget_data.json"

CATEGORIES = ["Food & Groceries", "Fuel", "Drink",
              "Shopping", "Entertainment"]
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")

def get_ai_advice(data, result):
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        
        history = data.get("history", [])
        savings = data.get("savings", {})
        target = savings.get("target", 0) if isinstance(savings, dict) else 0
        entries = savings.get("entries", []) if isinstance(savings, dict) else []
        total_saved = sum(e["amount"] for e in entries)
        
        history_summary = ""
        if len(history) > 1:
            for h in history[-3:]:
                history_summary += f"{h['month']}: Pay £{h['pay']}, Left over £{h['leftover']}, Status {h['status']}\n"
        
        cat_summary = ""
        for cat, amt in result["categories"].items():
            if amt > 0:
                cat_summary += f"{cat}: £{amt}\n"
        
        prompt = f"""You are a friendly personal financial advisor. Analyse this person's budget and give them SHORT, specific, actionable advice in 3-4 sentences maximum. Be warm, direct and personalised. Use their name. Focus on the most important insight from their data.

Name: {data['name']}
This month's pay: £{result['pay']}
Total bills: £{result['total_bills']}
Extra spending: £{result['extra']}
Left over: £{result['leftover']}
Status: {result['status']}

Spending breakdown:
{cat_summary}

Savings target: £{target}
Total saved so far: £{total_saved}

Recent history:
{history_summary if history_summary else 'First month using the app'}

Give personalised advice based on these real numbers. Be specific with amounts. Maximum 4 sentences."""

        message = client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=300,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        return message.content[0].text
        
    except Exception as e:
        print(f"AI advisor error: {e}")
        return None
def load_data(name=None):
    if USE_SUPABASE and name:
        try:
            result = supabase.table("budget_users").select("*").eq("name", name).execute()
            if result.data:
                print(f"Loaded from Supabase: {result.data[0]['name']}")
                return result.data[0]
        except Exception as e:
            print(f"Supabase load error: {e}")
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return None

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)
    if USE_SUPABASE:
        try:
            existing = supabase.table("budget_users").select("id").eq(
                "name", data["name"]).execute()
            if existing.data:
                result = supabase.table("budget_users").update({
                    "pay": data["pay"],
                    "bills": data["bills"],
                    "history": data.get("history", []),
                    "savings": data.get("savings", {}),
                    "pin": data.get("pin", "")
                }).eq("name", data["name"]).execute()
                print(f"Updated in Supabase!")
            else:
                result = supabase.table("budget_users").insert({
                    "name": data["name"],
                    "pay": data["pay"],
                    "bills": data["bills"],
                    "history": data.get("history", []),
                    "savings": data.get("savings", {}),
                    "pin": data.get("pin", "")
                }).execute()
                print(f"Inserted into Supabase!")
        except Exception as e:
            print(f"Supabase save error: {e}")

@app.route("/")
def index():
    name = request.cookies.get("user_name")
    if not name:
        return redirect(url_for("setup"))
    data = load_data(name)
    if not data:
        return redirect(url_for("setup"))
    return render_template("index.html",
                           data=data,
                           categories=CATEGORIES,
                           result=None,
                           today=datetime.now().strftime("%A %d %B %Y"))

@app.route("/calculate", methods=["POST"])
def calculate():
    name = request.cookies.get("user_name")
    if not name:
        return redirect(url_for("setup"))
    data = load_data(name)
    if not data:
        return redirect(url_for("setup"))

    pay = float(request.form.get("pay", 0))
    goal = float(request.form.get("goal", 0))
    note = request.form.get("note", "")

    cat_spending = {}
    for cat in CATEGORIES:
        try:
            cat_spending[cat] = float(request.form.get(cat, 0))
        except:
            cat_spending[cat] = 0

    total_bills = sum(b["amount"] for b in data["bills"])
    extra = sum(cat_spending.values())
    leftover = pay - total_bills - extra
    total_used = total_bills + extra
    pct = min(100, round((total_used / pay) * 100)) if pay > 0 else 0

    if leftover > 500:
        status = "GREEN"
        status_msg = "SMASHING IT THIS MONTH!"
    elif leftover > 200:
        status = "ORANGE"
        status_msg = "GETTING CLOSE TO LIMIT!"
    else:
        status = "RED"
        status_msg = "TOUGH MONTH!"

    tips = []
    if cat_spending.get("Drink", 0) > 150:
        tips.append(f"Your drink spending is £{cat_spending['Drink']:,.2f} — cutting back could save you £{cat_spending['Drink']*6:,.0f} in 6 months!")
    if extra > 0:
        biggest = max(cat_spending, key=cat_spending.get)
        if cat_spending[biggest] > 0:
            tips.append(f"Biggest extra cost: {biggest} at £{cat_spending[biggest]:,.2f}")
    if status == "GREEN" and leftover > 300:
        tips.append(f"Great month! Consider adding some of your £{leftover:,.2f} to your savings pot.")
    if status == "RED":
        tips.append("Your bills are taking up most of your income. Review subscriptions when they come up for renewal.")
    if not tips:
        tips.append("Good job keeping track of your finances this month!")

    current_month = datetime.now().strftime("%B %Y")
    history = data.get("history", [])
    if not isinstance(history, list):
        history = []

    existing_months = [h["month"] for h in history]
    new_entry = {
        "month": current_month,
        "pay": pay,
        "total_bills": round(total_bills, 2),
        "extra": round(extra, 2),
        "leftover": round(leftover, 2),
        "status": status,
        "note": note,
        "categories": cat_spending
    }
    if current_month in existing_months:
        for i, h in enumerate(history):
            if h["month"] == current_month:
                history[i] = new_entry
                break
    else:
        history.append(new_entry)

    data["history"] = history
    data["pay"] = pay
    save_data(data)

    result = {
        "pay": pay,
        "total_bills": total_bills,
        "extra": extra,
        "leftover": leftover,
        "pct": pct,
        "status": status,
        "status_msg": status_msg,
        "tips": tips,
        "goal": goal,
        "goal_hit": leftover >= goal if goal > 0 else None
    }

    result["categories"] = cat_spending
    ai_advice = get_ai_advice(data, result)

    return render_template("index.html",
                           data=data,
                           categories=CATEGORIES,
                           cat_spending=cat_spending,
                           result=result,
                           ai_advice=ai_advice,
                           today=datetime.now().strftime("%A %d %B %Y"))

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            pay = float(request.form.get("pay", 0))
        except:
            pay = 0
        bill_names = request.form.getlist("bill_name")
        bill_amounts = request.form.getlist("bill_amount")

        bills = []
        for n, a in zip(bill_names, bill_amounts):
            try:
                if n.strip():
                    bills.append({"name": n.strip(),
                                 "amount": float(a)})
            except:
                pass

        if name and pay > 0 and bills:
            existing = load_data(name)
            data = {
                "name": name,
                "pay": pay,
                "bills": bills,
                "history": existing.get("history", []) if existing else [],
                "savings": existing.get("savings", {}) if existing else {},
                "pin": existing.get("pin", "") if existing else ""
            }
            save_data(data)
            response = make_response(redirect(url_for("index")))
            response.set_cookie("user_name", name,
                               max_age=60*60*24*365)
            return response

    existing = load_data(request.cookies.get("user_name"))
    return render_template("setup.html", existing=existing)
@app.route("/history")
def history():
    name = request.cookies.get("user_name")
    if not name:
        return redirect(url_for("setup"))
    data = load_data(name)
    if not data:
        return redirect(url_for("setup"))
    history = data.get("history", [])
    if not isinstance(history, list):
        history = []
    return render_template("history.html",
                           data=data,
                           history=list(reversed(history)),
                           today=datetime.now().strftime("%A %d %B %Y"))
@app.route("/savings", methods=["GET", "POST"])
def savings():
    name = request.cookies.get("user_name")
    if not name:
        return redirect(url_for("setup"))
    data = load_data(name)
    if not data:
        return redirect(url_for("setup"))

    if request.method == "POST":
        action = request.form.get("action")
        if not isinstance(data.get("savings"), dict):
            data["savings"] = {"target": 0, "entries": []}

        if action == "set_target":
            try:
                target = float(request.form.get("target", 0))
                data["savings"]["target"] = target
            except:
                pass

        elif action == "add_to_pot":
            try:
                amount = float(request.form.get("amount", 0))
                note = request.form.get("note", "")
                if amount > 0:
                    if "entries" not in data["savings"]:
                        data["savings"]["entries"] = []
                    data["savings"]["entries"].append({
                        "amount": amount,
                        "note": note,
                        "date": datetime.now().strftime("%d %B %Y")
                    })
            except:
                pass

        save_data(data)
        return redirect(url_for("savings"))

    savings_data = data.get("savings", {"target": 0, "entries": []})
    if not isinstance(savings_data, dict):
        savings_data = {"target": 0, "entries": []}

    return render_template("savings.html",
                           data=data,
                           savings=savings_data,
                           today=datetime.now().strftime("%A %d %B %Y"))
@app.route("/dashboard")
def dashboard():
    name = request.cookies.get("user_name")
    if not name:
        return redirect(url_for("setup"))
    data = load_data(name)
    if not data:
        return redirect(url_for("setup"))
    history = data.get("history", [])
    if not isinstance(history, list):
        history = []
    return render_template("dashboard.html",
                           data=data,
                           history=history,
                           today=datetime.now().strftime("%A %d %B %Y"))

@app.route("/bills", methods=["GET", "POST"])
def bills():
    name = request.cookies.get("user_name")
    if not name:
        return redirect(url_for("setup"))
    data = load_data(name)
    if not data:
        return redirect(url_for("setup"))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            bill_name = request.form.get("bill_name", "").strip()
            try:
                bill_amount = float(request.form.get("bill_amount", 0))
                if bill_name and bill_amount > 0:
                    data["bills"].append({
                        "name": bill_name,
                        "amount": bill_amount
                    })
                    save_data(data)
            except:
                pass

        elif action == "remove":
            idx = int(request.form.get("idx", -1))
            if 0 <= idx < len(data["bills"]):
                data["bills"].pop(idx)
                save_data(data)

        return redirect(url_for("bills"))

    return render_template("bills.html",
                           data=data,
                           today=datetime.now().strftime("%A %d %B %Y"))
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
