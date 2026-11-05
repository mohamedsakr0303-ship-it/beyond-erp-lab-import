"""Business-type templates: one company file set up for the way that kind of business works.

Applying a template is additive and safe on live books: it never deletes an account, an entry or a screen's data.
It switches on the sections the business uses (the rest are hidden, not removed — Settings → Menus brings them back),
adds the accounts that business needs when they are missing, and sets the departments, job titles, pay items and
measured-work items that payroll and the crew calculator offer. Every choice stays editable afterwards.

The set-ups follow how established ERPs package industries (Odoo's industry apps; scaffolding-specific systems such as
Avontus Quantify for the rental side): track stock by where it is (yard / each site), bill rentals by period with a
minimum, charge loss and damage at a price list, price services (installation, dismantling, transport) separately
with their own cost, and keep labour per project so every site shows its own profit.
"""
import json

from . import accounting as acc
from . import care, payroll

# code, name, Arabic, type, subtype
_ACC = {
    "development": [("1220", "Land Held for Development", "أراضٍ بغرض التطوير", "Asset", "inventory"),
                    ("1225", "Units Under Construction (Development WIP)", "وحدات تحت الإنشاء", "Asset", "inventory"),
                    ("1230", "Completed Units for Sale", "وحدات تامة معدة للبيع", "Asset", "inventory"),
                    ("2140", "Unit Reservation Deposits", "جديات حجز الوحدات", "Liability", "deferred_revenue"),
                    ("5085", "Land & Development Cost of Units Sold", "تكلفة الأرض والتطوير للوحدات المباعة", "COGS", "cogs"),
                    ("6032", "Sales Commissions (brokers & agents)", "عمولات البيع (السماسرة)", "Expense", "marketing")],
    "investment": [("1705", "Investments in Listed Shares", "استثمارات في أسهم مقيدة", "Asset", "investment"),
                   ("1710", "Investments in Subsidiaries & Associates", "استثمارات في شركات تابعة وشقيقة", "Asset", "investment"),
                   ("1715", "Investment Property", "استثمارات عقارية", "Asset", "investment"),
                   ("1720", "Treasury Bills & Deposits", "أذون خزانة وودائع", "Asset", "investment"),
                   ("4091", "Dividend Income", "إيرادات توزيعات أرباح", "Revenue", "other_income"),
                   ("4092", "Gains on Sale of Investments", "أرباح بيع استثمارات", "Revenue", "other_income"),
                   ("4093", "Fair-Value Changes on Investments", "فروق تقييم الاستثمارات", "Revenue", "other_income"),
                   ("7055", "Losses on Sale of Investments", "خسائر بيع استثمارات", "Expense", "other_nonoperating")],
    "sales": [("6032", "Sales Commissions", "عمولات المبيعات", "Expense", "marketing"),
              ("6034", "Delivery & Freight Out", "مصروفات توصيل وشحن", "Expense", "other_operating"),
              ("4055", "Sales Returns", "مردودات المبيعات", "Revenue", "sales_discount")],
    "contracting": [("5025", "Plant & Machinery Running Costs", "تشغيل المعدات والآلات", "COGS", "cogs"),
                    ("5045", "Site Camp, Security & Utilities", "معسكر الموقع والحراسة والمرافق", "COGS", "cogs"),
                    ("6072", "Tendering & Bid Costs", "مصروفات المناقصات والعطاءات", "Expense", "other_operating")],
    "interior": [("5075", "Furniture & Accessories (client supply)", "أثاث وإكسسوارات (توريد للعميل)", "COGS", "cogs")],
    "scaffolding": [("5065", "Installation & Dismantling Crews", "أطقم التركيب والفك", "COGS", "cogs"),
                    ("5066", "Scaffold Repairs, Painting & Maintenance", "إصلاح ودهان وصيانة السقالات", "COGS", "cogs"),
                    ("5067", "Transport of Equipment", "نقل المعدات", "COGS", "cogs")],
    "restaurant": [("4026", "Food & Beverage Sales", "مبيعات الأطعمة والمشروبات", "Revenue", "operating_revenue"),
                   ("5011", "Food & Beverage Cost", "تكلفة الأطعمة والمشروبات", "COGS", "cogs"),
                   ("6033", "Delivery App Commissions", "عمولات تطبيقات التوصيل", "Expense", "marketing"),
                   ("6036", "Kitchen Gas & Utilities", "غاز ومرافق المطبخ", "Expense", "other_operating")],
    "services": [("4036", "Consulting & Professional Fees", "أتعاب استشارية ومهنية", "Revenue", "operating_revenue"),
                 ("5031", "Subcontracted Consultants & Freelancers", "استشاريون ومستقلون بالتعاقد", "COGS", "cogs")],
    "manufacturing": [("1205", "Raw Materials Inventory", "مخزون الخامات", "Asset", "inventory"),
                      ("1206", "Work in Process Inventory", "إنتاج تحت التشغيل", "Asset", "inventory"),
                      ("5012", "Raw Materials Used", "خامات مستخدمة في الإنتاج", "COGS", "cogs"),
                      ("5032", "Factory Labour", "أجور المصنع", "COGS", "cogs"),
                      ("5046", "Factory Overheads (power, maintenance)", "مصروفات صناعية غير مباشرة (كهرباء، صيانة)", "COGS", "cogs")],
    "logistics": [("4048", "Freight & Transport Revenue", "إيرادات النقل والشحن", "Revenue", "operating_revenue"),
                  ("5068", "Fuel & Tolls", "الوقود والرسوم", "COGS", "cogs"),
                  ("5069", "Drivers' Trip Allowances", "بدلات رحلات السائقين", "COGS", "cogs"),
                  ("5071", "Fleet Repairs & Tyres", "إصلاحات وإطارات الأسطول", "COGS", "cogs")],
    "general": [],
    "healthcare": care.ACCOUNTS,
    "homecare": care.ACCOUNTS,
}

TYPES = {
    "general": {"name": "General business", "ar": "نشاط عام", "hide": ["rentals", "shop", "estimating", "contracts", "equipment", "care"],
                "departments": ["Management", "Finance", "Sales", "Operations", "Admin"],
                "jobs": ["General Manager", "Accountant", "Sales Representative", "Administrator", "Driver", "Worker"],
                "bu": "Department / branch", "components": {"site": False},
                "guide": ["Use business units for branches or departments so each shows its own profit.",
                          "Record purchases as Operating Expenses or Direct Costs; stock items only if you hold stock."]},
    "interior": {"name": "Interior design & finishing", "ar": "التصميم الداخلي والتشطيبات", "hide": ["rentals", "shop", "care"],
                 "departments": ["Design", "Execution / Site", "Procurement", "Finance", "Admin"],
                 "jobs": ["Design Manager", "Interior Designer", "3D Visualiser", "Site Engineer", "Site Supervisor", "Foreman",
                          "Quantity Surveyor", "Accountant", "Technician", "Worker"],
                 "bu": "Project", "components": {"commission": False},
                 "guide": ["One business unit per project with its billing basis (cost-plus %, fixed price or settled).",
                           "Design fees, execution and supply are separate revenue accounts; BOQs and the price list feed offers.",
                           "Site workers on daily pay go on payroll as daily employees with the project as their business unit; "
                           "finishing subcontractors paid per m² use the crew calculator."]},
    "contracting": {"name": "Contracting & construction", "ar": "المقاولات العامة والإنشاءات", "hide": ["rentals", "shop", "care"],
                    "departments": ["Projects", "Technical Office", "Procurement", "Equipment", "QA / QC & Safety", "Finance", "Admin"],
                    "jobs": ["Project Manager", "Site Engineer", "Technical Office Engineer", "Quantity Surveyor", "Safety Officer",
                             "Foreman", "Storekeeper", "Equipment Operator", "Driver", "Accountant", "Worker"],
                    "bu": "Project / contract", "components": {"commission": False},
                    "guide": ["Each contract is a business unit; invoices are progress certificates (مستخلصات) with retention "
                              "(1150 retention receivable) and subcontractor retention (2110).",
                              "Issue materials to the project from stock, so its cost is in its profit when used.",
                              "Measured subcontract work (formwork m², rebar ton, blockwork m²) is priced in the crew calculator.",
                              "Contracts: the client contract and approved variation orders set the project's value; subcontracts are paid "
                              "by cumulative certificates (retention, advance recovery, VAT / withholding) — Projects & Contracts.",
                              "Site: purchase requests → quotes compared → orders; daily attendance; equipment hours and maintenance; "
                              "phases with % complete and tasks per project."]},
    "development": {"name": "Real-estate development", "ar": "التطوير العقاري", "hide": ["rentals", "care"],
                    "departments": ["Development", "Construction", "Sales & Marketing", "Customer Care", "Finance", "Legal", "Admin"],
                    "jobs": ["Development Manager", "Project Manager", "Site Engineer", "Sales Manager", "Property Consultant",
                             "Customer Service", "Collections Officer", "Legal Counsel", "Accountant"],
                    "bu": "Project / phase", "components": {"commission": True},
                    "guide": ["Land and construction costs build up in development WIP (1220 / 1225) per phase; completed units "
                              "move to 1230 and are released to cost of sales when each unit is sold.",
                              "Unit sales on installments with cheques run from Sales & showroom; reservation deposits sit in 2140.",
                              "Brokers' and consultants' commissions: payroll commission line or 6032."]},
    "sales": {"name": "Trading & sales", "ar": "التجارة والمبيعات", "hide": ["rentals", "estimating", "contracts", "equipment", "care"],
              "departments": ["Sales", "Warehouse", "Purchasing", "Delivery", "Finance", "Admin"],
              "jobs": ["Sales Manager", "Sales Representative", "Cashier", "Storekeeper", "Purchasing Officer", "Driver", "Accountant"],
              "bu": "Branch / channel", "components": {"commission": True},
              "guide": ["Products, stock and installments live in Sales & showroom; business units split branches or channels.",
                        "Pay commission as a monthly payroll line so it is taxed and costed with the salary."]},
    "investment": {"name": "Investment & holding", "ar": "الاستثمار والقابضة", "hide": ["rentals", "shop", "estimating", "stock", "projects", "contracts", "equipment", "care"],
                   "departments": ["Investments", "Finance", "Legal", "Admin"],
                   "jobs": ["Investment Manager", "Financial Analyst", "Accountant", "Legal Counsel", "Administrator"],
                   "bu": "Portfolio / investment", "components": {"commission": False, "site": False},
                   "guide": ["Each holding or property is a business unit; dividends, gains and fair-value changes have their own accounts.",
                             "Loans given and taken run from Loans; partners' capital and distributions from Partners."]},
    "scaffolding": {"name": "Scaffolding & equipment rental", "ar": "تأجير السقالات والمعدات", "hide": ["shop", "estimating", "contracts", "care"],
                    "departments": ["Operations / Yard", "Installation Crews", "Sales", "Finance", "Admin"],
                    "jobs": ["Operations Manager", "Yard Keeper", "Installation Supervisor", "Scaffolder", "Safety Inspector",
                             "Driver", "Sales Representative", "Accountant"],
                    "bu": "Rental contract / site", "components": {"commission": False},
                    "guide": ["Every piece type is a rental item with its monthly rent, loss / damage price and weight; buy pieces "
                              "as stock lots so the fleet's cost and depreciation are in the books.",
                              "Stock is tracked by where it is: in the yard or on each contract's site, through signed delivery and "
                              "return handovers (good / damaged / lost) — the return count bills losses at the price list.",
                              "Bill rent per period with a minimum; installation, dismantling and transport are separate services "
                              "with their own client price and crew / truck cost (rental calculator).",
                              "Crews paid per m² (frames × width × height) use the crew calculator; record what you actually paid.",
                              "Watch utilisation (pieces out ÷ owned) and payback (purchase cost ÷ monthly rent) per piece.",
                              "Rent (4045), installation and dismantling (4046) and transport (4047) are separate income lines, so "
                              "Reports → Profit by category shows what each one earned after its own costs.",
                              "A supplier discount or an extra cost after buying changes the lot's cost (Rental equipment → lot → "
                              "Adjust cost), not income; depreciation follows. At month end, book rent earned but not yet billed "
                              "(Rental contracts → Month-end accrual).",
                              "A funding partner repaid a share of every client payment: set the share in Partners → Distribution "
                              "policy; only what is due to him is held back from distributions.",
                              "Several partners can fund the company, each through his own account, alongside a bank (Partner "
                              "settlement → Other funding partners). Distributions can be paid through a funding partner's account, "
                              "and his balance can be moved to or from his capital (Partners → Funding account ⇄ capital)."]},
    "healthcare": {"name": "Clinics & medical centres", "ar": "العيادات والمراكز الطبية", "hide": ["rentals", "shop", "estimating", "contracts", "projects"],
                   "departments": ["Clinics / Outpatient", "Laboratory", "Radiology", "Nursing", "Reception & Billing", "Insurance & Claims",
                                   "Pharmacy & Stores", "Finance", "Admin"],
                   "jobs": ["Medical Director", "Consultant", "Specialist", "Resident Doctor", "Dentist", "Nurse", "Lab Technician",
                            "Radiology Technician", "Pharmacist", "Receptionist", "Insurance Coordinator", "Accountant", "Cleaner"],
                   "bu": "Branch / clinic / department", "components": {"commission": True},
                   "guide": ["Healthcare: patients (with their insurer / TPA, policy and coverage %), a price list of services (cash price, "
                             "contracted insurer price, the doctor's share, consumables), and appointments that become visits.",
                             "Billing a visit splits it into the patient's co-pay (cash / card at the desk) and the insurer's share "
                             "(1105, claimed monthly); revenue goes to 4061. The doctor's share is booked as a cost (5090) owed to "
                             "them (2115) — pay it from Payments, or through payroll.",
                             "Consumables (gloves, syringes, dressing kits) leave stock at cost on each visit (5091): keep them as stock "
                             "items in the clinic's store.",
                             "Insurance claims: gather a month's insurer shares into one claim; when the insurer pays, record what was "
                             "paid and what was rejected (4056).",
                             "Each branch, clinic or department is a business unit, so each shows its own profit. Medical services are "
                             "generally VAT-exempt; medical devices you buy may carry 5% VAT (2026)."]},
    "homecare": {"name": "Home healthcare", "ar": "الرعاية الصحية المنزلية", "hide": ["rentals", "shop", "estimating", "contracts", "projects", "equipment"],
                 "departments": ["Home Nursing", "Physiotherapy", "Home Doctors", "Elderly Care", "Care Coordination", "Finance", "Admin"],
                 "jobs": ["Medical Director", "Care Coordinator", "Home Visit Doctor", "Registered Nurse", "Assistant Nurse",
                          "Physiotherapist", "Caregiver / Elderly Companion", "Driver", "Accountant"],
                 "bu": "Service line / area team", "components": {"commission": True},
                 "guide": ["Home healthcare: each visit has the patient's address and district (to route the team), a travel fee "
                           "(4063), and check-in / check-out times — the record that the visit happened and how long a nursing "
                           "shift lasted.",
                           "Packages (12 physiotherapy sessions, a month of nursing) are paid in advance and held as prepaid (2108); "
                           "each session given moves its share to revenue (4062).",
                           "Nurses and therapists paid per visit or per shift: their share of each service is booked as a cost (5090) "
                           "owed to them (2115) and paid weekly or monthly; salaried staff stay on payroll.",
                           "Insured patients: the insurer's share is claimed monthly like a clinic; family-paid care is billed to the patient.",
                           "Consumables used at home (dressing kits, catheters) leave stock at cost on each visit."]},
    "restaurant": {"name": "Restaurants & cafés", "ar": "المطاعم والكافيهات", "hide": ["rentals", "estimating", "contracts", "equipment", "care", "projects"],
                   "departments": ["Kitchen", "Service / Floor", "Purchasing & Store", "Delivery", "Finance", "Admin"],
                   "jobs": ["Restaurant Manager", "Head Chef", "Cook", "Kitchen Helper", "Waiter", "Barista", "Cashier", "Delivery Rider",
                            "Storekeeper", "Accountant"],
                   "bu": "Branch / outlet", "components": {"commission": False},
                   "guide": ["Each branch is a business unit, so each shows its own profit.",
                             "Sales go to 4026 and food and drink bought to 5011: their ratio is your food cost %. Count the store "
                             "monthly (Inventory → Stock counts) so waste shows as a difference.",
                             "Delivery apps keep a commission: book it to 6033 (the model “Delivery app settlement” splits the payout).",
                             "Daily suppliers on credit (vegetables, meat, bread): use “Expense on credit” or a saved fill-in per supplier."]},
    "services": {"name": "Professional services & consulting", "ar": "الخدمات المهنية والاستشارات",
                 "hide": ["rentals", "shop", "estimating", "contracts", "equipment", "care", "stock"],
                 "departments": ["Consulting", "Delivery", "Business Development", "Finance", "Admin"],
                 "jobs": ["Managing Partner", "Senior Consultant", "Consultant", "Analyst", "Project Coordinator", "Accountant", "Administrator"],
                 "bu": "Client engagement", "components": {"commission": True},
                 "guide": ["Each client engagement is a business unit; invoice fees to 4036 and book freelancers you bring in to 5031, "
                           "so each engagement shows its own margin.",
                           "Retainers paid in advance are client advances: apply them to each month's invoice.",
                           "Track time with tasks and attendance; bill hours from the invoice lines."]},
    "manufacturing": {"name": "Manufacturing & workshops", "ar": "التصنيع والورش", "hide": ["rentals", "care", "estimating"],
                      "departments": ["Production", "Quality", "Maintenance", "Warehouse", "Sales", "Finance", "Admin"],
                      "jobs": ["Plant Manager", "Production Supervisor", "Machine Operator", "Technician", "Quality Inspector", "Storekeeper",
                               "Sales Representative", "Accountant", "Worker"],
                      "bu": "Production line / order", "components": {"commission": False},
                      "guide": ["Buy raw materials into stock (1205); issue them to production and the cost moves to 5012.",
                                "Factory wages go to 5032 and power and maintenance to 5046, so each line or order shows its full cost.",
                                "Use a business unit per production line or big order; finished goods sit in stock until sold."]},
    "logistics": {"name": "Transport & logistics", "ar": "النقل والخدمات اللوجستية", "hide": ["shop", "estimating", "contracts", "care"],
                  "departments": ["Operations / Dispatch", "Fleet & Maintenance", "Drivers", "Sales", "Finance", "Admin"],
                  "jobs": ["Operations Manager", "Dispatcher", "Driver", "Mechanic", "Fleet Supervisor", "Sales Representative", "Accountant"],
                  "bu": "Vehicle / route / client contract", "components": {"commission": False},
                  "guide": ["Each truck or route is a business unit: freight revenue (4048) against fuel and tolls (5068), drivers' "
                            "trip allowances (5069) and repairs (5071) shows each vehicle's profit.",
                            "Give drivers trip money as cash custody and settle it with their receipts; or pay fuel from the custody directly.",
                            "Vehicles are equipment: log their hours or kilometres and maintenance under Plant & equipment."]},
}
ORDER = ["general", "interior", "contracting", "development", "sales", "investment", "scaffolding", "healthcare", "homecare",
         "restaurant", "services", "manufacturing", "logistics"]

# entry models each business uses often, added to the company's models when the template is applied (only when every
# fixed account in them is the one this template expects). Sides as in entries.py; "lines" for more than two.
_MODELS = {
    "contracting": [{"id": "ind_sub_cert_retention", "name": "Subcontractor certificate with retention", "name_ar": "مستخلص مقاول باطن مع محتجزات",
                     "kind": "Direct Cost", "party": "required", "bu": "required",
                     "lines": [{"account": "role:subcontract_cost", "side": "debit"}, {"account": "role:sub_payable", "side": "credit"},
                               {"account": "role:retention_payable", "side": "credit"}]}],
    "interior": [{"id": "ind_client_supply", "name": "Client-supply furniture on credit", "name_ar": "أثاث توريد للعميل على الحساب",
                  "kind": "Direct Cost", "debit": "5075", "credit": "role:ap", "party": "required", "bu": "required"}],
    "scaffolding": [{"id": "ind_crew_owed", "name": "Installation crew work owed", "name_ar": "أعمال طاقم تركيب مستحقة", "kind": "Direct Cost",
                     "debit": "5065", "credit": "role:sub_payable", "party": "required", "bu": "optional"},
                    {"id": "ind_transport_owed", "name": "Equipment transport owed", "name_ar": "نقل معدات مستحق", "kind": "Direct Cost",
                     "debit": "5067", "credit": "role:ap", "party": "required", "bu": "optional"}],
    "development": [{"id": "ind_reservation", "name": "Unit reservation deposit received", "name_ar": "استلام جدية حجز وحدة", "kind": "Receipt Voucher",
                     "debit": "subtype:cash", "credit": "2140", "party": "required", "bu": "optional"},
                    {"id": "ind_broker_owed", "name": "Broker commission owed", "name_ar": "عمولة وسيط مستحقة", "kind": "Operating Expense",
                     "debit": "6032", "credit": "role:ap", "party": "required", "bu": "optional"}],
    "sales": [{"id": "ind_delivery_owed", "name": "Delivery cost owed to the courier", "name_ar": "تكلفة توصيل مستحقة لشركة الشحن",
               "kind": "Operating Expense", "debit": "6034", "credit": "role:ap", "party": "required", "bu": "optional"}],
    "investment": [{"id": "ind_dividend", "name": "Dividend received", "name_ar": "توزيعات أرباح مستلمة", "kind": "Receipt Voucher",
                    "debit": "subtype:cash", "credit": "4091", "party": "optional", "bu": "optional"}],
    "healthcare": [{"id": "ind_doctor_share", "name": "Doctor's share owed", "name_ar": "حصة طبيب مستحقة", "kind": "Direct Cost",
                    "debit": "5090", "credit": "2115", "party": "required", "bu": "optional"}],
    "homecare": [{"id": "ind_doctor_share", "name": "Caregiver's share owed", "name_ar": "حصة مقدم رعاية مستحقة", "kind": "Direct Cost",
                  "debit": "5090", "credit": "2115", "party": "required", "bu": "optional"}],
    "restaurant": [{"id": "ind_delivery_payout", "name": "Delivery app settlement", "name_ar": "تسوية تطبيق التوصيل", "kind": "Receipt Voucher",
                    "party": "required", "bu": "optional",
                    "lines": [{"account": "subtype:cash", "side": "debit"}, {"account": "6033", "side": "debit"}, {"account": "4026", "side": "credit"}]},
                   {"id": "ind_food_on_credit", "name": "Food supplies on credit", "name_ar": "مشتريات أطعمة على الحساب", "kind": "Direct Cost",
                    "debit": "5011", "credit": "role:ap", "party": "required", "bu": "optional"}],
    "services": [{"id": "ind_freelancer_owed", "name": "Freelancer work owed", "name_ar": "أعمال مستقل مستحقة", "kind": "Direct Cost",
                  "debit": "5031", "credit": "role:ap", "party": "required", "bu": "optional"}],
    "manufacturing": [{"id": "ind_materials_to_production", "name": "Raw materials issued to production", "name_ar": "صرف خامات للإنتاج",
                       "kind": "Journal Voucher", "debit": "5012", "credit": "1205", "party": "none", "bu": "optional"},
                      {"id": "ind_power_owed", "name": "Factory power bill owed", "name_ar": "فاتورة كهرباء المصنع المستحقة",
                       "kind": "Direct Cost", "debit": "5046", "credit": "role:ap", "party": "required", "bu": "optional"}],
    "logistics": [{"id": "ind_fuel_on_credit", "name": "Fuel on credit (station account)", "name_ar": "وقود على الحساب (حساب المحطة)",
                   "kind": "Direct Cost", "debit": "5068", "credit": "role:ap", "party": "required", "bu": "optional"},
                  {"id": "ind_trip_allowance", "name": "Driver trip allowance paid", "name_ar": "بدل رحلة سائق مصروف",
                   "kind": "Direct Cost", "debit": "5069", "credit": "subtype:cash", "party": "optional", "bu": "optional"}],
}


# e-invoice templates every business starts with (no activity code, branch or item code: those are the company's own,
# registered with the tax authority, and are filled in on the template or the tax settings)
_EINV_TEMPLATES = [
    {"code": "INV", "name": "Standard invoice", "doc_type": "I", "notes": "Used for every customer without a template of their own."},
    {"code": "CN-RET", "name": "Credit note: returns", "doc_type": "C", "reason": "Returned goods or cancelled work"},
    {"code": "CN-DISC", "name": "Credit note: discount after invoicing", "doc_type": "C", "reason": "Discount agreed after the invoice"},
    {"code": "DN-ADJ", "name": "Debit note: additional charges", "doc_type": "D", "reason": "Additional quantities or charges after the invoice"},
]


def _add_einv_templates(db):
    from . import einvoice
    added = []
    for t in _EINV_TEMPLATES:
        if not db.one("SELECT 1 FROM einv_templates WHERE code=?", (t["code"],)):
            einvoice.save_template(db, t)
            added.append(t["code"])
    return added


def _add_models(db, key, own_codes):
    """The business's own entry models, when their fixed accounts are this template's (or system ones): never one that
    would post to an account of the same number that means something else in these books."""
    from . import entries
    have = {t["id"] for t in entries.load(db)}
    names = {c: n for c, n, *_ in _ACC.get(key, [])}
    added = []
    for m in _MODELS.get(key, []):
        if m["id"] in have:
            continue
        specs = [ln["account"] for ln in m.get("lines", [])] or [m["debit"], m["credit"]]
        fixed = [x for x in specs if x and ":" not in x]
        if any(not db.one("SELECT 1 FROM accounts WHERE code=?" + (" AND name=?" if c in names else ""),
                          (c, names[c]) if c in names else (c,)) for c in fixed):
            continue
        added.append(m)
    if added:
        entries.save(db, entries.load(db) + added)
    return [m["id"] for m in added]


def catalogue():
    from .textl10n import UNITS
    return [{"key": k, "name": TYPES[k]["name"], "name_ar": TYPES[k]["ar"], "hide": TYPES[k]["hide"], "bu": TYPES[k]["bu"],
             "bu_ar": TYPES_AR.get(k, ("", []))[0], "guide_ar": TYPES_AR.get(k, ("", []))[1],
             "departments": TYPES[k]["departments"], "jobs": TYPES[k]["jobs"], "guide": TYPES[k]["guide"],
             "accounts": [{"code": c, "name": n, "name_ar": ar} for c, n, ar, *_ in _ACC.get(k, [])],
             "work_items": [{"item": i, "unit": u, "item_ar": WORK_AR.get(i, i), "unit_ar": UNITS.get(u, u)}
                            for i, u in payroll.DEFAULT_WORK_ITEMS.get(k, payroll.DEFAULT_WORK_ITEMS["general"])]}
            for k in ORDER]


def apply(db, key, hide_modules=True):
    if key not in TYPES:
        raise acc.ValidationError("Unknown business type")
    t = TYPES[key]
    added = []
    with db.tx():
        for code, name, ar, typ, sub in _ACC.get(key, []):
            if db.one("SELECT 1 FROM accounts WHERE code=?", (code,)) or db.one("SELECT 1 FROM accounts WHERE name=?", (name,)):
                continue
            acc.save_account(db, {"code": code, "name": name, "name_ar": ar, "type": typ, "subtype": sub})
            added.append(code)
        db.set_setting("business_type", key)
        if key in ("interior", "contracting"):          # design / fit-out / supervision offers fit these businesses
            from .documents import DESIGN_OFFER_TYPES
            for ot in DESIGN_OFFER_TYPES:
                if not db.one("SELECT 1 FROM offer_types WHERE name=?", (ot["name"],)):
                    db.conn.execute("INSERT INTO offer_types(name, content) VALUES (?,?)",
                                    (ot["name"], json.dumps({k: v for k, v in ot.items() if k != "name"}, ensure_ascii=False)))
        if hide_modules:
            db.set_setting("hidden_modules", json.dumps(t["hide"]))
        db.set_setting("payroll_departments", json.dumps(t["departments"], ensure_ascii=False))
        db.set_setting("payroll_jobs", json.dumps(t["jobs"], ensure_ascii=False))
        s = payroll.settings(db)
        for c in s["components"]:
            if c["code"] in t["components"]:
                c["active"] = t["components"][c["code"]]
        payroll.save_settings(db, {"components": s["components"]})
        models = _add_models(db, key, added)
        einv = _add_einv_templates(db)
    return {"type": key, "accounts_added": added, "models_added": models, "einv_templates_added": einv, "hidden": t["hide"] if hide_modules else None}


# ---- the same guide in Arabic (catalogue() sends both; the screen shows the reader's language)
TYPES_AR = {
    "general": ("قسم / فرع", [
        "استخدم وحدات الأعمال للفروع أو الأقسام ليظهر ربح كل منها على حدة.",
        "سجّل المشتريات كمصروفات تشغيل أو تكاليف مباشرة؛ وأصناف المخزون فقط إن كان لديك مخزون."]),
    "interior": ("مشروع", [
        "وحدة أعمال لكل مشروع بطريقة محاسبته (التكلفة + نسبة، سعر ثابت، أو تسوية).",
        "أتعاب التصميم والتنفيذ والتوريد حسابات إيراد منفصلة؛ والمقايسات وقائمة الأسعار تغذي العروض.",
        "عمال الموقع باليومية يُسجّلون في الرواتب كموظفين يوميين على وحدة أعمال المشروع؛ ومقاولو التشطيب بالمتر يُحسبون في حاسبة الأطقم."]),
    "contracting": ("مشروع / عقد", [
        "كل عقد وحدة أعمال؛ والفواتير مستخلصات جارية بتأمين أعمال (1150 تأمين أعمال لدى العملاء) وتأمين مقاولي الباطن (2110).",
        "اصرف الخامات للمشروع من المخزن لتدخل تكلفته في ربحه عند الاستخدام.",
        "أعمال مقاولي الباطن بالقياس (شدات م²، حديد طن، مباني م²) تُسعَّر في حاسبة الأطقم.",
        "العقود: عقد العميل وأوامر التغيير المعتمدة تحدد قيمة المشروع؛ ومقاولو الباطن يُصرف لهم بمستخلصات تراكمية (تأمين، استرداد دفعة مقدمة، ضريبة قيمة مضافة / خصم) — المشروعات والعقود.",
        "الموقع: طلبات شراء ← مقارنة عروض ← أوامر شراء؛ حضور يومي؛ ساعات وصيانة المعدات؛ مراحل بنسب إنجاز ومهام لكل مشروع."]),
    "development": ("مشروع / مرحلة", [
        "تتجمع تكاليف الأرض والإنشاء في أعمال التطوير تحت التنفيذ (1220 / 1225) لكل مرحلة؛ والوحدات المكتملة تنتقل إلى 1230 وتُحمّل على تكلفة المبيعات عند بيع كل وحدة.",
        "بيع الوحدات بالتقسيط والشيكات من المبيعات والمعرض؛ ومقدمات الحجز في 2140.",
        "عمولات الوسطاء والاستشاريين: بند عمولة في الرواتب أو حساب 6032."]),
    "sales": ("فرع / قناة بيع", [
        "المنتجات والمخزون والأقساط في المبيعات والمعرض؛ ووحدات الأعمال تفصل الفروع أو قنوات البيع.",
        "اصرف العمولة كبند شهري في الرواتب لتُحسب ضريبتها وتكلفتها مع الراتب."]),
    "investment": ("محفظة / استثمار", [
        "كل مساهمة أو عقار وحدة أعمال؛ ولتوزيعات الأرباح والأرباح الرأسمالية وفروق التقييم حسابات خاصة بها.",
        "القروض الممنوحة والمستلمة من شاشة القروض؛ ورأس مال الشركاء والتوزيعات من شاشة الشركاء."]),
    "scaffolding": ("عقد تأجير / موقع", [
        "كل نوع قطعة صنف تأجير بإيجاره الشهري وسعر الفقد / التلف ووزنه؛ واشترِ القطع كدفعات مخزون لتدخل تكلفة الأسطول وإهلاكه الدفاتر.",
        "المخزون يُتابع حسب مكانه: في الساحة أو في موقع كل عقد، عبر محاضر تسليم واستلام موقعة (سليم / تالف / مفقود) — وعد المرتجع يحاسب المفقود بقائمة الأسعار.",
        "فوترة الإيجار لكل فترة بحد أدنى؛ والتركيب والفك والنقل خدمات منفصلة بسعرها للعميل وتكلفة الطاقم / السيارة (حاسبة التأجير).",
        "الأطقم المدفوع لها بالمتر (الشبابيك × العرض × الارتفاع) تُحسب في حاسبة الأطقم؛ وسجّل ما دفعته فعلاً.",
        "تابع نسبة التشغيل (القطع المؤجرة ÷ المملوكة) واسترداد التكلفة (سعر الشراء ÷ الإيجار الشهري) لكل قطعة.",
        "الإيجار (4045) والتركيب والفك (4046) والنقل (4047) إيرادات منفصلة، فيعرض «التقارير ← الربح حسب الفئة» ربح كل منها بعد تكاليفه.",
        "خصم المورد أو التكلفة الإضافية بعد الشراء يغيّر تكلفة الدفعة (معدات التأجير ← الدفعة ← تعديل التكلفة) لا الإيراد، ويتبعه الإهلاك. وفي آخر الشهر سجّل الإيجار المستحق غير المفوتر (عقود التأجير ← استحقاق آخر الشهر).",
        "الشريك الممول الذي يُسدَّد له نصيب من كل دفعة عميل: حدد النصيب من الشركاء ← سياسة التوزيع؛ ولا يُحتجز من التوزيع إلا المستحق له.",
        "يمكن لأكثر من شريك تمويل الشركة، كلٌّ من حسابه الخاص، بجانب البنك (تسوية الشركاء ← شركاء ممولون آخرون). ويمكن صرف التوزيعات من خلال حساب الشريك الممول، ونقل رصيده إلى رأس ماله أو منه (الشركاء ← حساب التمويل ⇄ رأس المال)."]),
    "healthcare": ("فرع / عيادة / قسم", [
        "الرعاية الصحية: المرضى (بشركة التأمين / إدارة المطالبات، والوثيقة ونسبة التغطية)، وقائمة أسعار الخدمات (السعر النقدي، سعر التعاقد مع التأمين، حصة الطبيب، المستهلكات)، ومواعيد تتحول إلى زيارات.",
        "فوترة الزيارة تقسمها إلى حصة المريض (نقداً / ببطاقة في الاستقبال) وحصة التأمين (1105، تُطالب شهرياً)؛ والإيراد في 4061. حصة الطبيب تُسجّل كتكلفة (5090) مستحقة له (2115) — تُصرف من المدفوعات أو عبر الرواتب.",
        "المستهلكات (جوانتيات، سرنجات، أطقم غيار) تخرج من المخزون بالتكلفة مع كل زيارة (5091): احتفظ بها كأصناف في مخزن العيادة.",
        "مطالبات التأمين: اجمع حصص الشهر في مطالبة واحدة؛ وعند السداد سجّل المدفوع والمرفوض (4056).",
        "كل فرع أو عيادة أو قسم وحدة أعمال ليظهر ربحه. الخدمات الطبية معفاة غالباً من ضريبة القيمة المضافة؛ وقد تخضع الأجهزة الطبية المشتراة لضريبة 5% (2026)."]),
    "homecare": ("خط خدمة / فريق منطقة", [
        "الرعاية المنزلية: لكل زيارة عنوان المريض ومنطقته (لتوجيه الفريق) ورسم انتقال (4063) وأوقات الوصول والمغادرة — إثبات حدوث الزيارة ومدة وردية التمريض.",
        "الباقات (12 جلسة علاج طبيعي، شهر تمريض) تُدفع مقدماً وتُحفظ كإيراد مقدم (2108)؛ وكل جلسة تنقل حصتها إلى الإيراد (4062).",
        "الممرضون والمعالجون بالزيارة أو بالوردية: حصتهم من كل خدمة تكلفة (5090) مستحقة لهم (2115) تُصرف أسبوعياً أو شهرياً؛ والموظفون بالراتب يبقون في الرواتب.",
        "المرضى المؤمَّن عليهم: حصة التأمين تُطالب شهرياً مثل العيادة؛ والرعاية التي تدفعها الأسرة تُفوتر للمريض.",
        "المستهلكات المستخدمة بالمنزل (أطقم غيار، قساطر) تخرج من المخزون بالتكلفة مع كل زيارة."]),
    "restaurant": ("فرع / منفذ", [
        "كل فرع وحدة أعمال ليظهر ربح كل منها.",
        "المبيعات في 4026 والأطعمة والمشروبات المشتراة في 5011: نسبتهما هي نسبة تكلفة الطعام. اجرد المخزن شهرياً (المخزون ← الجرد) ليظهر الهدر كفرق.",
        "تطبيقات التوصيل تخصم عمولة: سجّلها في 6033 (نموذج «تسوية تطبيق التوصيل» يقسم المبلغ المحوَّل).",
        "الموردون اليوميون على الحساب (خضار، لحوم، خبز): استخدم «مصروف على الحساب» أو تعبئة محفوظة لكل مورد."]),
    "services": ("تكليف عميل", [
        "كل تكليف لعميل وحدة أعمال؛ فوتر الأتعاب في 4036 وسجّل المستقلين الذين تستعين بهم في 5031 ليظهر هامش كل تكليف.",
        "الاشتراكات المدفوعة مقدماً دفعات مقدمة من العملاء: سوّها مع فاتورة كل شهر.",
        "تابع الوقت بالمهام والحضور؛ وفوتر الساعات من بنود الفاتورة."]),
    "manufacturing": ("خط إنتاج / أمر", [
        "اشترِ الخامات إلى المخزون (1205)؛ واصرفها للإنتاج فتنتقل تكلفتها إلى 5012.",
        "أجور المصنع في 5032 والكهرباء والصيانة في 5046، فيظهر لكل خط أو أمر تكلفته الكاملة.",
        "استخدم وحدة أعمال لكل خط إنتاج أو أمر كبير؛ والإنتاج التام يبقى في المخزون حتى يُباع."]),
    "logistics": ("مركبة / خط سير / عقد عميل", [
        "كل شاحنة أو خط سير وحدة أعمال: إيراد النقل (4048) مقابل الوقود والرسوم (5068) وبدلات رحلات السائقين (5069) والإصلاحات (5071) يُظهر ربح كل مركبة.",
        "أعطِ السائقين مصروف الرحلة كعهدة نقدية وسوِّها بإيصالاتهم؛ أو ادفع الوقود من العهدة مباشرة.",
        "المركبات معدات: سجّل ساعاتها أو كيلومتراتها وصيانتها من شاشة المعدات والآلات."]),
}
WORK_AR = {"Frame scaffold — installation": "سقالة شبابيك — تركيب", "Frame scaffold — dismantling": "سقالة شبابيك — فك",
           "Loading / unloading a truck": "تحميل / تفريغ سيارة", "Wall painting": "دهان حوائط", "Gypsum-board ceiling": "سقف جبس بورد",
           "Floor tiling": "تبليط أرضيات", "Skirting": "وزرة", "Electrical point": "نقطة كهرباء", "Formwork": "شدات خشبية",
           "Rebar fixing": "تركيب حديد التسليح", "Blockwork": "مباني طوب", "Plastering": "بياض", "Excavation": "حفر",
           "Finishing works": "أعمال تشطيب", "Commission per unit sold": "عمولة لكل وحدة مباعة", "Site visit": "زيارة موقع",
           "Hours worked": "ساعات العمل", "Pieces": "قطع", "Area": "مساحة"}
