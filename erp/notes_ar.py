"""Arabic for the column notes of the import/export sheets that the app's own texts do not carry (the hints on each
heading in the Excel templates and the workbook). Looked up by the English note; the workbook and the import templates
use it through workbook_ar.translations()."""

NOTES_AR = {
    "names separated by ; — blank = any customer": "أسماء مفصولة بـ ; — فارغ = أي عميل",
    "filled in by the app": "يملؤه التطبيق",
    "B = business (tax ID), P = person (national ID), F = foreign": "B = منشأة (رقم ضريبي)، P = شخص طبيعي (رقم قومي)، F = أجنبي",
    "supplier, subcontractor, driver, transport, technician, consultant, customer, employee, other — or your own":
        "مورد، مقاول باطن، سائق، نقل، فني، استشاري، عميل، موظف، أخرى — أو تصنيفك الخاص",
    "unit code: %; unit code: %": "كود الوحدة: %؛ كود الوحدة: %",
    "Codes, prefixes (60*) or ranges (6010-6040); blank = any shared cost": "أكواد أو بدايات (60*) أو نطاقات (6010-6040)؛ فارغ = أي تكلفة مشتركة",
    "0 = not depreciated (land)": "0 = لا يُهلك (الأراضي)",
    "blank = the company's depreciation policy": "فارغ = سياسة الإهلاك في الشركة",
    "2 = double declining": "2 = القسط المتناقص المضاعف",
    "Yes (blank) = the statements here include depreciation up to the report end, like the app's depreciation run. "
    "No = it is already in the Journal Vouchers (books exported from the app)":
        "نعم (فارغ) = تتضمن القوائم هنا الإهلاك حتى نهاية التقرير كما يحتسبه التطبيق. لا = الإهلاك مسجل بالفعل في قيود اليومية (دفاتر مصدّرة من التطبيق)",
    "The last full month by the report end (Settings → report_to)": "آخر شهر كامل حتى نهاية التقرير (الإعدادات ← report_to)",
    "Accumulated: before registering + every full month up to the report end (the app's method: straight line on what is "
    "left, or declining balance switching to straight line); never more than cost − residual":
        "المجمع: ما قبل التسجيل + كل شهر كامل حتى نهاية التقرير (طريقة التطبيق: قسط ثابت على المتبقي، أو متناقص يتحول إلى ثابت)؛ "
        "ولا يزيد أبداً على التكلفة − قيمة الخردة",
    "in every warehouse that holds it, unless the warehouse has its own (next columns)": "في كل مخزن يحتوي الصنف، ما لم يكن للمخزن حده الخاص (الأعمدة التالية)",
    "the usual buying cost per unit — kept even with no stock": "تكلفة الشراء المعتادة للوحدة — تُحفظ حتى بدون رصيد",
    "Warehouse: qty; Warehouse: qty": "المخزن: الكمية؛ المخزن: الكمية",
    "both (blank) = notification and on screen; notify; visual = on screen only; none":
        "إشعار وتنبيه على الشاشة (فارغ)؛ إشعار فقط؛ على الشاشة فقط؛ بدون تنبيه",
    "for the suggested minimum": "للحد الأدنى المقترح",
    "Yes = the minimum follows the forecast's suggestion": "نعم = الحد الأدنى يتبع اقتراح التوقع",
    "blank = automatic; an existing code is updated": "فارغ = تلقائي؛ الكود الموجود يُحدَّث",
    "stock items: the warehouse a sale takes them from. Blank: the app uses the first warehouse in the list that holds "
    "enough, the Excel workbook the first warehouse — name it so both cost the sale the same way":
        "أصناف المخزون: المخزن الذي يُصرف منه البيع. فارغ: يستخدم التطبيق أول مخزن به كمية كافية، وملف الإكسل أول مخزن — "
        "حدده ليحتسب الاثنان تكلفة البيع بنفس الطريقة",
    "e.g. 1210 Goods & Units for Sale — when sold, its cost moves to cost of sales":
        "مثال: 1210 بضاعة ووحدات بغرض البيع — عند البيع تنتقل تكلفتها إلى تكلفة المبيعات",
    "Label: value; Label: value — e.g. Area: 150 m²; Floor: 3": "البيان: القيمة؛ البيان: القيمة — مثال: المساحة: 150 م²؛ الدور: 3",
    "Blank = cost × (1 + markup)": "فارغ = التكلفة × (1 + الهامش)",
    "EGS code registered on the e-invoice portal": "كود EGS المسجل على منظومة الفاتورة الإلكترونية",
    "keep = company earnings (cost stays at full price) · pass = lower cost · blank = the business unit's rule":
        "تبقى إيراداً للشركة (التكلفة بالسعر الكامل) · تخفيض للتكلفة · فارغ = قاعدة وحدة العمل",
    "Optional: the project stage (phase) this cost belongs to": "اختياري: مرحلة المشروع التي تخصها هذه التكلفة",
    "Shared costs only: a rule code, or company to keep it as company cost. Blank = by the rules":
        "للتكاليف المشتركة فقط: كود قاعدة توزيع، أو company لإبقائها على الشركة. فارغ = حسب القواعد",
    "Only when the receipt is booked straight to revenue: the amount is VAT-inclusive":
        "فقط عند تسجيل المقبوض مباشرة كإيراد: المبلغ شامل ضريبة القيمة المضافة",
    "blank = the next number on the business unit": "فارغ = الرقم التالي على وحدة العمل",
    "blank = what the client paid before the date (receipts)": "فارغ = ما سدده العميل قبل التاريخ (المقبوضات)",
    "Yes = final (ختامي); blank = running (جاري)": "نعم = ختامي؛ فارغ = جاري",
    "cost-plus: a fee on top of each section's work, e.g. Electrical=10%; HVAC=8%; *=12% (* = every other section)":
        "التكلفة + نسبة: أتعاب فوق أعمال كل قسم، مثال: Electrical=10%; HVAC=8%; *=12% (* = باقي الأقسام)",
    "blank = invoice; a credit / debit note names its original invoice and reason": "فارغ = فاتورة؛ إشعار الخصم / الإضافة يذكر فاتورته الأصلية والسبب",
    "a code from 'E-Invoice Templates'; blank = by customer": "كود من «قوالب الفاتورة الإلكترونية»؛ فارغ = حسب العميل",
    "filled in by the app (the signed document)": "يملؤه التطبيق (المستند الموقّع)",
    "blank = 100 %; e.g. half a month of rent = 50 %": "فارغ = 100 %؛ مثال: نصف شهر إيجار = 50 %",
    "blank = the invoice's revenue account": "فارغ = حساب إيراد الفاتورة",
    "The deal this payment settles (one supplier can have several deals)": "الاتفاق الذي يسدده هذا الدفع (قد يكون للمورد عدة اتفاقات)",
    "Receipts: qty x unit cost. Issues, transfers and count losses: FIFO, the cost of the oldest units still in that warehouse "
    "(or the moving average, if Settings says stock_costing = average); sales of stock items (Sales Contracts) take their units "
    "in date order too. A count gain: its unit cost if given, else what a unit on hand is worth. Keep the rows in date order.":
        "الاستلام: الكمية × تكلفة الوحدة. الصرف والتحويل وعجز الجرد: الوارد أولاً يصرف أولاً، أي تكلفة أقدم وحدات ما زالت في ذلك المخزن "
        "(أو المتوسط المتحرك إذا كانت الإعدادات stock_costing = average)؛ ومبيعات أصناف المخزون (عقود البيع) تأخذ وحداتها بترتيب التاريخ أيضاً. "
        "زيادة الجرد: بتكلفة الوحدة إن ذُكرت، وإلا بقيمة الوحدة الموجودة. حافظ على ترتيب الصفوف بالتاريخ.",
    "Only when a line belongs to someone other than the voucher's party (e.g. each partner in a share transfer)":
        "فقط عندما يخص السطر جهة غير جهة القيد (مثل كل شريك في نقل حصة)",
    "Item line: the item or material (debit = qty × unit price, made by the app). Leave empty on normal lines":
        "سطر صنف: الصنف أو الخامة (المدين = الكمية × سعر الوحدة، يحسبه التطبيق). اتركه فارغاً في السطور العادية",
    "A warehouse name (received into stock) — or empty: used on the voucher's business unit":
        "اسم مخزن (استلام للمخزون) — أو فارغ: يُستخدم على وحدة العمل الخاصة بالقيد",
    "Vouchers with no business unit: a rule code, or company to keep it as company cost. Blank = by the rules":
        "القيود بدون وحدة عمل: كود قاعدة توزيع، أو company لإبقائها على الشركة. فارغ = حسب القواعد",
    "The person's sign-in name: their start / end of day fills their attendance": "اسم الدخول للشخص: بداية / نهاية يومه تملأ حضوره",
    "usually the month end": "عادةً نهاية الشهر",
    "blank = automatic; needed to add repayments in Loan Transactions": "فارغ = تلقائي؛ مطلوب لإضافة السداد في حركات القروض",
    "money (default) or opening": "money (افتراضي) أو opening (رصيد افتتاحي)",
    "12 = 12 %": "12 = 12 %",
    "1, 2, 3, 4, 6 or 12": "1 أو 2 أو 3 أو 4 أو 6 أو 12",
    "pay = installment (principal + interest); interest = accrue interest; draw = more money; writeoff":
        "pay = قسط (أصل + فائدة)؛ interest = استحقاق فوائد؛ draw = سحب مبلغ إضافي؛ writeoff = إعدام",
    "blank = automatic; needed to add expense reports": "فارغ = تلقائي؛ مطلوب لإضافة تسويات المصروفات",
    "blank = automatic": "فارغ = تلقائي",
    "10 = 10 %": "10 = 10 %",
    "blank = paid in full": "فارغ = مسدد بالكامل",
    "what the crew / truck costs us — for the services profit": "تكلفة الطاقم / السيارة علينا — لحساب ربح الخدمات",
    "filled = the PO was received": "مملوء = تم استلام أمر الشراء",
    "filled with the PO = contract signed": "مملوء مع أمر الشراء = تم توقيع العقد",
    "links the contract to its extracts and receipts": "يربط العقد بمستخلصاته ومقبوضاته",
    "filled (with the PO and both signatures) = the signed delivery: the pieces are on site":
        "مملوء (مع أمر الشراء والتوقيعين) = التسليم الموقّع: القطع في الموقع",
    "rent already billed on an extract up to this date — the app bills from the next day":
        "الإيجار المفوتر بالفعل على مستخلص حتى هذا التاريخ — يفوتر التطبيق من اليوم التالي",
    "or leave blank and write the description": "أو اتركه فارغاً واكتب الوصف",
    "blank = the product's price": "فارغ = سعر المنتج",
    "booked as one receipt when confirmed": "يُسجَّل كمقبوض واحد عند التأكيد",
    "e.g. CIB 2026-09; blank = one statement per account": "مثال: CIB 2026-09؛ فارغ = كشف واحد لكل حساب",
    "1, 3, 6, 12…": "1، 3، 6، 12…",
    "blank = automatic; needed to attach add-ons": "فارغ = تلقائي؛ مطلوب لإرفاق الإضافات",
    "e.g. PO-0101": "مثال: PO-0101",
    "cost needs a business unit; stock a warehouse": "التكلفة تحتاج وحدة عمل؛ والمخزون يحتاج مخزناً",
    "e.g. CC-0001 / SC-0001": "مثال: CC-0001 / SC-0001",
}
