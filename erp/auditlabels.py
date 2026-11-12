"""The audit log's actions ("account.add", "po.receive") as words in the reader's language ("Add account" / "إضافة حساب").

An action is recorded as <object>.<verb> (sometimes <object>.<sub>.<verb>); the label is the verb and the object, each
from the lists below. Anything not listed reads as its words ("rent lot adjust"), never as a code.
"""
import re

OBJECTS = {
    "account": ("account", "حساب"), "ai": ("assistant", "المساعد الذكي"), "allocation": ("allocation", "التوزيع"),
    "rule": ("rule", "قاعدة"), "api_key": ("API key", "مفتاح API"), "approval": ("approval", "اعتماد"), "asset": ("fixed asset", "أصل ثابت"),
    "attachment": ("attachment", "مرفق"), "attendance": ("attendance", "الحضور"), "bank": ("bank", "البنك"), "statement": ("statement", "كشف"),
    "bill": ("bill", "فاتورة مورد دورية"), "branding": ("branding", "الهوية"), "image": ("image", "صورة"), "bu": ("business unit", "وحدة أعمال"),
    "bulk": ("bulk action", "إجراء جماعي"), "care": ("care", "الرعاية"), "claim": ("claim", "مطالبة"), "cash": ("cash", "النقدية"),
    "certificate": ("certificate", "مستخلص مقاول"), "chat": ("chat", "المحادثة"), "room": ("room", "غرفة"), "contract": ("contract", "عقد"),
    "cost_item": ("cost item", "بند تكلفة"), "crew": ("crew sheet", "يومية عمال"), "custody": ("cash custody", "عهدة"),
    "dashboard": ("dashboard", "لوحة المتابعة"), "group": ("group", "مجموعة"), "demo": ("demo data", "بيانات تجريبية"),
    "depreciation": ("depreciation", "الإهلاك"), "doc": ("document", "مستند"), "einvoice": ("e-invoice", "الفاتورة الإلكترونية"),
    "template": ("template", "قالب"), "employee": ("employee", "موظف"), "entry_model": ("entry model", "نموذج قيد"), "equity": ("equity", "حقوق الملكية"),
    "funding": ("funding", "التمويل"), "ownership": ("ownership", "الملكية"), "ereceipt": ("e-receipt", "إيصال إلكتروني"),
    "eta": ("e-invoice", "الفاتورة الإلكترونية"), "settings": ("settings", "الإعدادات"), "extract": ("invoice", "فاتورة"),
    "forecast": ("forecast", "التوقعات"), "assumptions": ("assumptions", "الافتراضات"), "funders": ("funding partners", "الشركاء الممولون"),
    "import": ("import", "الاستيراد"), "sheet": ("sheet", "ورقة"), "industry": ("industry template", "قالب النشاط"), "item": ("stock item", "صنف"),
    "minimum": ("minimum quantity", "الحد الأدنى"), "journal": ("journal entry", "قيد يومية"), "licence": ("licence", "الترخيص"),
    "loan": ("loan", "قرض"), "login": ("sign-in", "تسجيل الدخول"), "offer": ("offer", "عرض سعر"), "partners": ("partners", "الشركاء"),
    "stage_shares": ("stage shares", "حصص المراحل"), "party": ("contact", "جهة تعامل"), "payroll": ("payroll", "الرواتب"), "run": ("run", "مسير"),
    "po": ("purchase order", "أمر شراء"), "pr": ("purchase request", "طلب شراء"), "preset": ("saved fill-in", "تعبئة محفوظة"),
    "product": ("product", "منتج"), "rates": ("price list", "قائمة الأسعار"), "recurring": ("recurring entry", "قيد متكرر"),
    "rent": ("rental", "التأجير"), "lot": ("stock lot", "دفعة معدات"), "rental": ("rental contract", "عقد إيجار"), "handover": ("handover", "تسليم"),
    "request": ("employee request", "طلب موظف"), "sale": ("sale", "عملية بيع"), "cheque": ("cheque", "شيك"), "setup": ("setup", "الإعداد الأول"),
    "stock": ("stock", "المخزون"), "stockcount": ("stock count", "جرد"), "subscription": ("subscription", "اشتراك"),
    "subscriptions": ("subscriptions", "الاشتراكات"), "support": ("support", "الدعم"), "ticket": ("ticket", "تذكرة"),
    "tax_code": ("tax code", "كود ضريبة"), "two-step": ("two-step sign-in", "التحقق بخطوتين"), "undo": ("undo", "تراجع"),
    "user": ("user", "مستخدم"), "variation": ("variation", "أمر تغيير"), "workday": ("workday", "يوم عمل"), "year": ("financial year", "السنة المالية"),
    "erase": ("all data", "كل البيانات"), "policy": ("policy", "السياسة"), "key": ("key", "المفتاح"),
}
VERBS = {
    "add": ("Add", "إضافة"), "edit": ("Edit", "تعديل"), "delete": ("Delete", "حذف"), "save": ("Save", "حفظ"), "approve": ("Approve", "اعتماد"),
    "reject": ("Reject", "رفض"), "submit": ("Submit", "إرسال"), "pay": ("Pay", "سداد"), "post": ("Post", "ترحيل"), "void": ("Void", "إلغاء"),
    "reverse": ("Reverse", "عكس"), "cancel": ("Cancel", "إلغاء"), "sign": ("Sign", "توقيع"), "issue": ("Issue", "إصدار"),
    "receive": ("Receive", "استلام"), "award": ("Award", "ترسية"), "convert": ("Convert", "تحويل"), "dispose": ("Dispose of", "استبعاد"),
    "run": ("Run", "تشغيل"), "close": ("Close", "إقفال"), "reopen": ("Reopen", "إعادة فتح"), "merge": ("Merge", "دمج"),
    "transfer": ("Transfer", "تحويل"), "load": ("Load", "تحميل"), "apply": ("Apply", "تطبيق"), "activate": ("Activate", "تفعيل"),
    "create": ("Create", "إنشاء"), "archive": ("Archive", "أرشفة"), "export": ("Export", "تصدير"), "settle": ("Settle", "تسوية"),
    "unbill": ("Unbill", "إلغاء الفوترة"), "bill": ("Bill", "فوترة"), "tag": ("Tag", "تصنيف"), "revoke": ("Revoke", "إلغاء"),
    "on": ("Turn on", "تفعيل"), "off": ("Turn off", "إيقاف"), "reset": ("Reset", "إعادة ضبط"), "next": ("Next number of", "الرقم التالي لـ"),
    "capture": ("Capture", "تسجيل"), "accrual": ("Accrue", "استحقاق"), "invoice": ("Invoice", "فوترة"), "adjust": ("Adjust", "تسوية"),
    "undo": ("Undo", "تراجع عن"), "dry": ("Check", "فحص"),
}


SPECIAL = {"rent.lot_adjust": ("Adjust stock lot", "تسوية دفعة معدات"), "rent.lot": ("Stock lot", "دفعة معدات"),
           "attachment": ("Attachment", "مرفق"), "stock": ("Stock movement", "حركة مخزون")}


def _words(k, ar):
    o = OBJECTS.get(k)
    return o[1 if ar else 0] if o else k.replace("_", " ").replace("-", " ")


def label(action, lang="en"):
    """One action as words: verb + object ("Add account"), object alone when there is no verb ("Sign-in")."""
    ar = lang == "ar"
    parts = [p for p in re.split(r"\.", str(action or "")) if p]
    if not parts:
        return ""
    if action in SPECIAL:
        return SPECIAL[action][1 if ar else 0]
    verb = VERBS.get(parts[-1]) if len(parts) > 1 else None
    objs = parts[:-1] if verb else parts
    # English reads outer → inner ("payroll run"); Arabic puts the main noun first ("مسير الرواتب")
    obj = " ".join(_words(p, ar) for p in (reversed(objs) if ar else objs))
    if verb:
        out = f"{verb[1]} {obj}" if ar else f"{verb[0]} {obj}"
    else:
        out = obj
    return out[:1].upper() + out[1:] if not ar else out
