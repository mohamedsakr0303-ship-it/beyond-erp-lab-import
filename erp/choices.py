"""Words for the fixed choices of the import templates' drop-downs, in the template's language, and the way back.

The importer has always compared against codes ("other_current_asset", "Yes", "monthly"). A template made for an
Arabic user now offers Arabic words in its drop-downs (an English one plain English), and `to_code` turns whatever the
cell holds (the code, the English or Arabic word, or the word with its " — examples" tail) back into the code, so
old files, new files and files typed by hand all import the same.

Statement lines (the Chart of Accounts' "بند القوائم المالية") also say what belongs there, with everyday examples, and
the template lists only the lines of the Type chosen on that row.
"""
import re

from .db import SUBTYPES

TYPE_LABELS = {"Asset": ("Asset", "أصول"), "Liability": ("Liability", "التزامات"), "Equity": ("Equity", "حقوق الملكية"),
               "Revenue": ("Revenue", "إيرادات"), "COGS": ("Cost of sales", "تكلفة المبيعات"), "Expense": ("Expense", "مصروفات")}

# statement line: (English, Arabic, what goes here — English, Arabic)
SUBTYPE_INFO = {
    "cash": ("Cash & cash equivalents", "النقدية وما في حكمها",
             "cash in hand, petty cash, bank accounts, wallets", "الخزينة، صندوق المصروفات النثرية، الحسابات البنكية، المحافظ الإلكترونية"),
    "receivable": ("Accounts receivable", "المدينون (العملاء)",
                   "customers who owe you, cheques under collection, retentions held by customers",
                   "العملاء المدينون، أوراق القبض، محتجزات لدى العملاء"),
    "allowance_doubtful": ("Allowance for doubtful accounts", "مخصص الديون المشكوك فيها",
                           "provision against customers who may not pay (reduces receivables)", "مخصص لما قد لا يُحصَّل من العملاء (يُخصم من المدينين)"),
    "inventory": ("Inventory", "المخزون", "materials, goods and units held for sale", "خامات وبضائع ووحدات بغرض البيع"),
    "prepaid": ("Prepaid expenses & advances", "مصروفات مقدمة ودفعات مقدمة",
                "rent or insurance paid in advance, advances to suppliers, input VAT",
                "إيجار أو تأمين مدفوع مقدماً، دفعات مقدمة للموردين، ضريبة المدخلات"),
    "other_current_asset": ("Other current assets", "أصول متداولة أخرى",
                            "employee cash custody, employee advances and loans, deposits, other debtors due within a year",
                            "العهد النقدية لدى العاملين، سلف وقروض العاملين، التأمينات المؤقتة، مدينون آخرون خلال سنة"),
    "fixed_asset": ("Property & equipment", "الأصول الثابتة", "land, buildings, vehicles, furniture, computers, site equipment",
                    "أراضٍ، مبانٍ، سيارات، أثاث، أجهزة حاسب، معدات مواقع"),
    "accumulated_depreciation": ("Accumulated depreciation", "مجمع الإهلاك", "total depreciation of fixed assets (reduces them)",
                                 "مجموع إهلاك الأصول الثابتة (يُخصم منها)"),
    "intangible": ("Intangible assets", "أصول غير ملموسة", "software, licences, trademarks, website, goodwill",
                   "برامج وتراخيص، علامات تجارية، موقع إلكتروني، شهرة"),
    "accumulated_amortization": ("Accumulated amortization", "مجمع الاستهلاك", "total amortization of intangibles (reduces them)",
                                 "مجموع استهلاك الأصول غير الملموسة (يُخصم منها)"),
    "investment": ("Long-term investments", "استثمارات طويلة الأجل", "shares in other companies, long deposits",
                   "مساهمات في شركات أخرى، ودائع طويلة الأجل"),
    "other_noncurrent_asset": ("Other non-current assets", "أصول غير متداولة أخرى", "long-term deposits paid, loans given for over a year",
                               "تأمينات لدى الغير طويلة الأجل، قروض ممنوحة لأكثر من سنة"),
    "payable": ("Accounts payable", "الدائنون (الموردون)", "suppliers and subcontractors you owe, cheques issued",
                "الموردون ومقاولو الباطن الدائنون، أوراق الدفع"),
    "accrued": ("Accrued liabilities", "مصروفات مستحقة", "salaries, social insurance and expenses owed but not yet paid",
                "رواتب وتأمينات اجتماعية ومصروفات مستحقة لم تُدفع بعد"),
    "deferred_revenue": ("Deferred revenue", "إيرادات مؤجلة", "customer advances, money received before the work is done",
                         "دفعات مقدمة من العملاء، مبالغ محصلة قبل تنفيذ العمل"),
    "tax_payable": ("Taxes payable", "ضرائب مستحقة", "output VAT, withholding tax, income tax owed",
                    "ضريبة المخرجات، الخصم والتحصيل، ضريبة الدخل المستحقة"),
    "dividends_payable": ("Profit distributions payable", "توزيعات أرباح مستحقة", "profit declared for partners and not yet paid",
                          "أرباح معلنة للشركاء لم تُصرف بعد"),
    "other_current_liability": ("Other current liabilities", "التزامات متداولة أخرى",
                                "customer security deposits, retentions held for subcontractors, other creditors within a year",
                                "تأمينات العملاء، محتجزات مقاولي الباطن، دائنون آخرون خلال سنة"),
    "short_term_debt": ("Short-term debt", "قروض قصيرة الأجل", "bank loans and credit cards due within a year",
                        "قروض وتسهيلات بنكية وبطاقات ائتمان خلال سنة"),
    "long_term_debt": ("Long-term debt", "قروض طويلة الأجل", "loans due after more than a year, partner loans",
                       "قروض تستحق بعد أكثر من سنة، قروض الشركاء"),
    "other_noncurrent_liability": ("Other non-current liabilities", "التزامات غير متداولة أخرى", "end-of-service provision, long deposits",
                                   "مخصص مكافأة نهاية الخدمة، تأمينات طويلة الأجل"),
    "capital": ("Capital", "رأس المال", "partners' paid-in capital", "رأس المال المدفوع من الشركاء"),
    "partner_current": ("Partners' current accounts", "جاري الشركاء", "running accounts with each partner", "الحساب الجاري لكل شريك"),
    "reserves": ("Reserves", "الاحتياطيات", "legal and general reserves", "الاحتياطي القانوني والعام"),
    "retained_earnings": ("Retained earnings", "الأرباح المحتجزة", "profits of past years kept in the company", "أرباح السنوات السابقة المحتفظ بها"),
    "drawings": ("Drawings / dividends", "المسحوبات / التوزيعات", "what partners take out during the year", "ما يسحبه الشركاء خلال السنة"),
    "other_equity": ("Other equity", "حقوق ملكية أخرى", "share premium, other equity items", "علاوة إصدار وبنود ملكية أخرى"),
    "operating_revenue": ("Operating revenue", "إيرادات النشاط", "sales, contract revenue, fees from your main business",
                          "المبيعات، إيرادات العقود، الأتعاب من نشاطك الرئيسي"),
    "sales_discount": ("Sales discounts & allowances", "خصومات ومسموحات المبيعات", "discounts given to customers (reduces revenue)",
                       "خصومات ممنوحة للعملاء (تُخصم من الإيرادات)"),
    "other_income": ("Other income", "إيرادات أخرى", "interest received, gains on selling assets, rent from others",
                     "فوائد دائنة، أرباح بيع أصول، إيجارات من الغير"),
    "cogs": ("Cost of sales", "تكلفة المبيعات", "materials, subcontractors, direct labour, hired equipment for the work you sell",
             "خامات، مقاولو باطن، عمالة مباشرة، معدات مستأجرة للأعمال المباعة"),
    "employee": ("Employee expenses", "مصروفات العاملين", "administrative salaries, social insurance, staff benefits",
                 "رواتب إدارية، تأمينات اجتماعية، مزايا العاملين"),
    "general_admin": ("General & administrative", "مصروفات عمومية وإدارية", "rent, software, professional fees, stationery, bank charges",
                      "إيجار، برامج، أتعاب مهنية، أدوات مكتبية، مصروفات بنكية"),
    "marketing": ("Marketing & advertising", "تسويق وإعلان", "ads, sponsorships, marketing materials", "إعلانات، رعايات، مواد تسويقية"),
    "utilities": ("Utilities", "مرافق", "electricity, water, phone, internet", "كهرباء، مياه، هاتف، إنترنت"),
    "other_operating": ("Other operating expenses", "مصروفات تشغيلية أخرى", "travel, fuel, maintenance, other running costs",
                        "انتقالات، وقود، صيانة، مصروفات تشغيل أخرى"),
    "depreciation": ("Depreciation", "مصروف الإهلاك", "the year's depreciation of fixed assets", "إهلاك الأصول الثابتة عن الفترة"),
    "amortization": ("Amortization", "مصروف الاستهلاك", "the year's amortization of intangibles", "استهلاك الأصول غير الملموسة عن الفترة"),
    "interest": ("Interest expense", "مصروف الفوائد", "interest on loans and facilities", "فوائد القروض والتسهيلات"),
    "other_nonoperating": ("Other non-operating expenses", "مصروفات غير تشغيلية أخرى", "losses on selling assets, write-offs, currency losses",
                           "خسائر بيع أصول، إعدامات، فروق عملة"),
    "income_tax": ("Income tax", "ضريبة الدخل", "the company's income tax for the period", "ضريبة الدخل على أرباح الفترة"),
}
SEP = " — "


def _human(code):
    s = str(code)
    return s.replace("_", " ").capitalize() if re.fullmatch(r"[a-z][a-z0-9_]*", s) else s


def label(sheet, key, code, ar=False, long=False):
    """The word the drop-down shows for a code (with what goes there, for statement lines when `long`)."""
    if code in ("", None):
        return ""
    if key == "subtype" and code in SUBTYPE_INFO:
        en, a, hen, har = SUBTYPE_INFO[code]
        return (a + (SEP + har if long else "")) if ar else (en + (SEP + hen if long else ""))
    if key == "type" and sheet == "Chart of Accounts" and code in TYPE_LABELS:
        return TYPE_LABELS[code][1 if ar else 0]
    if ar:
        from .workbook_ar import code_meaning
        return code_meaning(sheet, key, str(code)) or _human(code)
    return _human(code)


def _norm(s):
    s = re.split(r"\s[—–-]\s", str(s))[0].strip().lower().replace("&", " and ")     # without the " — examples" tail
    s = re.sub(r"[إأآ]", "ا", s).replace("ة", "ه").replace("ى", "ي")
    s = re.sub(r"(^|\s)(?:ال)(?=[\u0600-\u06FF]{2})", r"\1", s)                     # الشهر = شهر
    return re.sub(r"[\s_]+", " ", s).strip()


_maps = {}


def to_code(sheet, col, v):
    """A cell's value in a choice column → the code the importer expects (unchanged when it is not one of the words)."""
    choices = col.get("choices") or []
    if not choices or not isinstance(v, str) or not v.strip():
        return v
    if v in choices:
        return v
    k = (sheet, col["key"])
    m = _maps.get(k)
    if m is None:
        m = {}
        for c in choices:
            if isinstance(c, str) and c:
                for w in (c, label(sheet, col["key"], c), label(sheet, col["key"], c, ar=True)):
                    m.setdefault(_norm(w), c)
        _maps[k] = m
    return m.get(_norm(v), v)


# the standard product details offered on the product screen (app.js SPEC_PRESETS): their Arabic names for documents
SPEC_AR = {"Area": "المساحة", "Floor": "الدور", "Bedrooms": "غرف النوم", "Bathrooms": "الحمامات", "View": "الإطلالة",
           "Finishing": "التشطيب", "Delivery": "الاستلام", "Brand": "الماركة", "Model": "الموديل", "Size": "المقاس",
           "Colour": "اللون", "Material": "الخامة", "Warranty": "الضمان", "Duration": "المدة", "Includes": "يشمل"}


def spec_name(k, ar):
    """A product detail's name for the reader: a standard one in Arabic when the document is Arabic; the company's own
    names as they were typed."""
    return SPEC_AR.get(str(k).strip(), k) if ar else k


def subtypes_by_type():
    return {t: list(v) for t, v in SUBTYPES.items()}
