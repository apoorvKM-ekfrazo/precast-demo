# 🏗️ Apex Precast — AI Plant Intelligence Pilot
**Athlone, Co. Westmeath | Pilot v0.1**

End-to-End Intelligence: Mix → Inventory → Dispatch → Traceability

---

## What This Is
A working Streamlit pilot that demonstrates AI-driven operations intelligence
for a precast concrete manufacturer. Built for Apex Precast as a believable
proof-of-concept before full productionisation.

All data is synthetic but calibrated to realistic Irish precast operations
(volumes, distances, Irish road factors, concrete grading, admixture rates).

---

## Setup

### 1. Create virtual environment (recommended)
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Generate synthetic data
```bash
python data_generator.py
```
This writes 6 JSON files to the `data/` folder — plant, sites, inventory,
trucks, orders, and batch history. Run again anytime to reset.

### 4. Run the app
```bash
streamlit run app.py
```

### 5. Add Groq API Key
In the sidebar, paste your Groq API key (starts with `gsk_`).
This enables:
- AI Order Parsing (Order Intake tab)
- Plant Intelligence Copilot (AI Copilot tab)

---

## The 6 Modules

| Tab | What it does |
|-----|-------------|
| 🏭 Overview | KPI dashboard + delivery site map |
| 📦 Inventory | Stock levels, batch capacity calc, reorder alerts |
| 📋 Order Intake | Order queue + AI parsing from plain-English text |
| 🚛 Smart Dispatch | Greedy scheduler + Gantt timeline + route map |
| 🔍 Traceability | Batch ID lookup → full journey + QA report |
| 💬 AI Copilot | Natural language plant queries powered by Groq LLM |

---

## Key Technical Decisions

**Slump Risk Model:** Concrete workability degrades after 90 minutes from
batching. The scheduler flags any delivery where (load time + travel time +
pour time) exceeds 70 minutes as at-risk. Assessed per truckload (max 8m³),
not per order — because each mixer drum has its own 90-minute clock.

**Dispatch Algorithm:** Greedy earliest-available assignment with road-factor
correction (straight-line × 1.35) for Irish road geometry. Transit mixer speed
is set to 65 km/h loaded, 80 km/h returning empty.

**Slump risk > MEDIUM:** The scheduler still assigns these but flags them
prominently. In production, these would trigger an automated alert to either:
(a) use a retarder admixture, (b) arrange local batching, or
(c) reject and reschedule.

---

## Connecting to Real Data (Phase 2)

The `data/` JSON files mirror the exact shape of what a real MES/ERP export
would look like. To move to production:

1. Replace `data_generator.py` output with live API calls to your ERP
2. Add a FastAPI backend with endpoints matching the blueprint spec
3. Connect to a real database (PostgreSQL/Supabase recommended)
4. Replace Groq with your preferred LLM provider (Groq, OpenAI, Anthropic)

---

## Project Structure

```
oreilly-precast-ai/
├── app.py                  Main Streamlit application (6 tabs)
├── data_generator.py       Synthetic data generator
├── requirements.txt        Python dependencies
├── data/
│   ├── plant.json          Plant location details
│   ├── sites.json          8 delivery sites across Ireland
│   ├── inventory.json      7 raw materials with stock levels
│   ├── trucks.json         6 transit mixer trucks
│   ├── orders.json         12 sample orders
│   └── batches.json        20 historical batch records
└── utils/
    ├── scheduler.py        Dispatch scheduling logic + slump risk
    └── llm_client.py       Groq API: order parser + copilot
```

---

## Demo Script (for client meetings)

1. **Start on Overview tab** — show the KPI bar and the site map with order circles
2. **Inventory tab** — switch grade to C50, note which material limits production first
3. **Order Intake** — paste the WhatsApp sample, click Parse, show it extracts structure
4. **Add the order** — watch it appear in the queue
5. **Dispatch tab** — show Gantt and explain slump risk colours
6. **Point to the HIGH risk row** — "this is 97km, concrete would be unusable on arrival"
7. **Traceability** — pick any batch, scroll through the journey, show QA panel
8. **Copilot** — ask "What materials need reordering urgently?" live
