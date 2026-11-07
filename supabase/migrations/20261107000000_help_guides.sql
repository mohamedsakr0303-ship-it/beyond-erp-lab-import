-- Help & FAQ guides in English and Arabic: the complicated parts of the ERP, with worked examples (every entry
-- table is what the app itself posts for that example), pictures and short clips from the site's help/ folder.
-- Added once: a guide the team has already edited, unpublished or deleted is never put back or overwritten.

insert into public.help_categories(key, name, name_ar, icon, sort) values ($g$projects$g$, $g$Projects and subcontractors$g$, $g$المشروعات ومقاولو الباطن$g$, $g$🏗️$g$, 4) on conflict (key) do nothing;
insert into public.help_categories(key, name, name_ar, icon, sort) values ($g$partners$g$, $g$Partners and profit$g$, $g$الشركاء والأرباح$g$, $g$🤝$g$, 5) on conflict (key) do nothing;

create table if not exists public.help_seeded (key text primary key, at timestamptz not null default now());
alter table public.help_seeded enable row level security;

with s as (insert into public.help_seeded(key) values ($g$guide-2026-11-01$g$) on conflict (key) do nothing returning key)
insert into public.help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
select $g$start$g$, $g$Getting started: set up a new company in the right order$g$, $g$البداية: إعداد شركة جديدة بالترتيب الصحيح$g$, $g$Each step below depends on the one before it, so following this order saves rework later.

## 1. Choose the business type
Open **Administration → Industry Templates** and press **Apply to this company** on the type closest to your work. The template turns on the screens you need, adds the accounts that type uses, and sets up payroll departments, job titles and piece-rate work items. You can change the menus later, but applying the right template first is the fastest start.

![Industry Templates: each card says what it switches on and adds](help/start-industry-en.jpg)

## 2. Check the chart of accounts
Open **General Accounting → Chart of Accounts**. Every account belongs to a statement line (for example *Trade receivables* or *General & administrative*), and some accounts carry a system role (the bank, receivables, output VAT…). The app posts automatically to the accounts that carry those roles.
- Rename accounts and add sub-accounts freely.
- Never reuse an account number for something different: old entries keep the number.

![Chart of Accounts](help/start-coa-en.jpg)

## 3. Add business units
Open **Business Units → Business Units (Cost Centres)**. Create one unit for each project, branch or department you want to see the profit of. For a project, also enter:
- the **billing basis** (cost-plus, fixed price, flexible or live fee);
- the **expected revenue** and **expected cost %**. These tell the app how much cost is still to come, which is held back from the partners' distributable profit (see *Partners: how profit available to distribute is worked out*).

## 4. Enter the opening balances
Bring in what the business owns and owes on the start date, including stock at cost. The guide *Opening balances* walks through a full example.

## 5. Add people and permissions
Open **Administration → Users & Permissions**. Give each person only the screens and actions they need (view, add, edit). A user can also be set to send entries for approval instead of posting them.

## 6. Bring in your data
**Administration → Data Import & Export** takes Excel (our template or your own sheets), Google Sheets and older BEYOND workbooks. See *Importing from Excel, Google Sheets or an older BEYOND workbook*.

## 7. Check and lock
- **Financial Reports → Model Audit** checks at any time that the trial balance, balance sheet, cash flow and stock all agree.
- When a month is final, set **System Settings → System & data → Books closed up to (lock date)**. Nothing dated on or before it can be posted or changed.

> Tip: practise on a separate demo company first (choose *Demo company (sample data)* when creating it). Never load demo data into your real books.$g$, $g$كل خطوة تعتمد على ما قبلها، لذلك يوفر عليك هذا الترتيب إعادة العمل لاحقاً.

## 1. اختر نوع النشاط
افتح **الإدارة والنظام ← قوالب الأنشطة** واضغط **تطبيق على هذه الشركة** على النوع الأقرب لعملك. يفعّل القالب الشاشات التي تحتاجها، ويضيف الحسابات الخاصة بهذا النشاط، ويضبط أقسام الرواتب والمسميات الوظيفية وبنود أعمال الأطقم بالإنتاج. يمكنك تعديل القوائم لاحقاً، لكن تطبيق القالب المناسب أولاً هو أسرع بداية.

![قوالب الأنشطة: كل بطاقة توضح ما تفعّله وما تضيفه](help/start-industry-ar.jpg)

## 2. راجع دليل الحسابات
افتح **المحاسبة العامة ← دليل الحسابات**. كل حساب ينتمي إلى بند في القوائم المالية (مثل *ذمم مدينة* أو *مصروفات عمومية وإدارية*)، وبعض الحسابات لها دور في النظام (البنك، العملاء، ضريبة القيمة المضافة على المخرجات…). يرحّل البرنامج تلقائياً إلى الحسابات التي تحمل هذه الأدوار.
- يمكنك إعادة تسمية الحسابات وإضافة حسابات فرعية بحرية.
- لا تستخدم رقم حساب لشيء مختلف أبداً: القيود القديمة تحتفظ بالرقم.

![دليل الحسابات](help/start-coa-ar.jpg)

## 3. أضف وحدات الأعمال
افتح **وحدات الأعمال ← وحدات الأعمال (مراكز التكلفة)**. أنشئ وحدة لكل مشروع أو فرع أو قسم تريد معرفة ربحه. وللمشروع أدخل أيضاً:
- **أساس الفوترة** (التكلفة + نسبة، سعر ثابت، مرن، أو نسبة حية)؛
- **الإيراد المتوقع** و**نسبة التكلفة المتوقعة**. منهما يعرف البرنامج التكلفة المتبقية، وتُحجز من الربح القابل للتوزيع على الشركاء (انظر *الشركاء: كيف يُحسب الربح القابل للتوزيع*).

## 4. أدخل الأرصدة الافتتاحية
سجّل ما تملكه الشركة وما عليها في تاريخ البداية، بما في ذلك المخزون بالتكلفة. يشرح دليل *الأرصدة الافتتاحية* مثالاً كاملاً.

## 5. أضف المستخدمين والصلاحيات
افتح **الإدارة والنظام ← المستخدمون والصلاحيات**. امنح كل شخص الشاشات والإجراءات التي يحتاجها فقط (عرض، إضافة، تعديل). ويمكن ضبط المستخدم ليرسل قيوده للاعتماد بدلاً من ترحيلها مباشرة.

## 6. استورد بياناتك
تقبل **الإدارة والنظام ← استيراد وتصدير البيانات** ملفات Excel (قالبنا أو أوراقك الخاصة) وGoogle Sheets ومصنفات BEYOND السابقة. انظر *الاستيراد من Excel أو Google Sheets أو مصنف BEYOND سابق*.

## 7. راجع ثم أقفل
- تفحص **التقارير المالية ← مراجعة الدفاتر** في أي وقت أن ميزان المراجعة والميزانية والتدفقات النقدية والمخزون كلها متطابقة.
- عندما يصبح الشهر نهائياً اضبط **إعدادات النظام ← النظام والبيانات ← الدفاتر مقفلة حتى تاريخ**. لا يمكن بعدها ترحيل أو تعديل أي شيء بتاريخ مساوٍ له أو قبله.

> نصيحة: تدرّب أولاً على شركة تجريبية منفصلة (اختر *شركة تجريبية (بيانات نموذجية)* عند إنشائها). لا تحمّل البيانات التجريبية في دفاترك الحقيقية أبداً.$g$, 1, true, 'BeyondERP'
from s where exists (select 1 from public.help_categories where key = $g$start$g$);

with s as (insert into public.help_seeded(key) values ($g$guide-2026-11-02$g$) on conflict (key) do nothing returning key)
insert into public.help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
select $g$start$g$, $g$Opening balances: moving an existing business in (with a worked example)$g$, $g$الأرصدة الافتتاحية: نقل نشاط قائم إلى البرنامج (مع مثال محلول)$g$, $g$Opening balances are what the business owns and owes on the day you start using BeyondERP. Choose a clean start date, usually the first day after the last closed month or year (here **1 January 2026**), and take the balances from the trial balance of your old books on the day before.

## Step 1: opening stock, as a goods receipt
Enter stock first, so each item starts with its quantity **and its cost**: FIFO needs that cost to value later issues.
1. **Inventory → Stock Movements → + New**, type **Goods receipt**.
2. Date 1 January 2026, warehouse, item (*Wall putty 20kg*), quantity **200**, unit cost **150** (the cost in the old books).
3. Supplier: type **Opening balances**, and **Paid now: 0**.

The app posts:
| Account | Debit | Credit |
|---|---|---|
| 1200 Materials Inventory | 30,000.00 |  |
| 2010 Trade Payables — Suppliers |  | 30,000.00 |
| **Total** | **30,000.00** | **30,000.00** |

The 30,000 owed to the supplier "Opening balances" is not a real debt. Step 2 clears it.

## Step 2: everything else, in one journal voucher
**General Accounting → Journal Entries → + New**, dated 1 January 2026. One line for each balance:
- cash and each bank account;
- receivables **one line per customer**, with the customer's name in the line, so aging and statements are right from day one;
- payables **one line per supplier**, with the supplier's name;
- fixed assets **at cost**, and their accumulated depreciation on its own line;
- a debit of 30,000 on payables for the supplier **Opening balances**, to clear step 1;
- partners' capital, and **retained earnings** as the difference.

| Account | Party | Debit | Credit |
|---|---|---|---|
| 1010 Cash on Hand |  | 25,000.00 |  |
| 1020 Cash at Bank |  | 410,000.00 |  |
| 1100 Trade Receivables — Customers | Al Noor Developments | 120,000.00 |  |
| 1400 Furniture, Fixtures & Office Equipment |  | 80,000.00 |  |
| 1490 Accumulated Depreciation |  |  | 20,000.00 |
| 2010 Trade Payables — Suppliers | Delta Ceramics |  | 95,000.00 |
| 2010 Trade Payables — Suppliers | Opening balances | 30,000.00 |  |
| 3010 Partners' Capital |  |  | 300,000.00 |
| 3100 Retained Earnings |  |  | 250,000.00 |
| **Total** |  | **665,000.00** | **665,000.00** |

![The posted opening entry: debits and credits both 665,000.00](help/opening-jv-en.jpg)

## Check the result
- **Trial Balance**: debits = credits = 665,000.00.
- **Balance Sheet** at 1 January 2026: assets 645,000.00 = liabilities 95,000.00 + equity 550,000.00.
- **Accounts Payable**: only Delta Ceramics shows (95,000.00); "Opening balances" nets to zero.
- **Stock Balances**: 200 bags of wall putty, value 30,000.00.

> If retained earnings comes out very different from the old books, a balance was missed or entered on the wrong side. Compare account by account with the old trial balance before posting anything else.

## Fixed assets you want depreciated automatically
Instead of the two fixed-asset lines in the journal, add each asset in **Assets & Treasury → Fixed Asset Register**. Choose **Recording: Already recorded (opening balance): register only** and fill in **Prior accumulated depreciation (if any)**. The register then continues the depreciation from where the old books stopped. Keep the asset's cost and accumulated depreciation in the opening journal so the balance sheet is complete.

## Then lock the opening date
Set **System Settings → System & data → Books closed up to (lock date)** to 1 January 2026, so the opening balances can't be changed by accident.$g$, $g$الأرصدة الافتتاحية هي ما تملكه الشركة وما عليها في اليوم الذي تبدأ فيه استخدام BeyondERP. اختر تاريخ بداية واضحاً، غالباً أول يوم بعد آخر شهر أو سنة مقفلة (هنا **1 يناير 2026**)، وخذ الأرصدة من ميزان مراجعة دفاترك القديمة في اليوم السابق.

## الخطوة 1: المخزون الافتتاحي كسند استلام
أدخل المخزون أولاً، حتى يبدأ كل صنف بكميته **وتكلفته**: تحتاج طريقة الوارد أولاً يُصرف أولاً (FIFO) هذه التكلفة لتقييم الصرف لاحقاً.
1. **المخزون ← حركات المخزون ← + جديد**، النوع **استلام**.
2. التاريخ 1 يناير 2026، والمخزن، والصنف (*معجون حوائط 20 كجم*)، والكمية **200**، وتكلفة الوحدة **150** (التكلفة في الدفاتر القديمة).
3. المورد: اكتب **أرصدة افتتاحية**، و**المدفوع الآن: 0**.

يرحّل البرنامج:
| الحساب | مدين | دائن |
|---|---|---|
| 1200 مخزون الخامات | 30,000.00 |  |
| 2010 الموردون (ذمم دائنة تجارية) |  | 30,000.00 |
| **الإجمالي** | **30,000.00** | **30,000.00** |

مبلغ 30,000 المستحق للمورد «أرصدة افتتاحية» ليس ديناً حقيقياً، وتقفله الخطوة 2.

## الخطوة 2: باقي الأرصدة في قيد يومية واحد
**المحاسبة العامة ← قيود اليومية ← + جديد** بتاريخ 1 يناير 2026، بسطر لكل رصيد:
- النقدية وكل حساب بنكي؛
- العملاء **بسطر لكل عميل** مع اسمه في السطر، لتكون أعمار الديون وكشوف الحساب صحيحة من اليوم الأول؛
- الموردون **بسطر لكل مورد** مع اسمه؛
- الأصول الثابتة **بالتكلفة**، ومجمع إهلاكها في سطر مستقل؛
- مدين 30,000 على الموردين للمورد **أرصدة افتتاحية** لإقفال الخطوة 1؛
- رأس مال الشركاء، و**الأرباح المحتجزة** بالفرق.

| الحساب | الطرف | مدين | دائن |
|---|---|---|---|
| 1010 النقدية بالصندوق |  | 25,000.00 |  |
| 1020 النقدية بالبنوك |  | 410,000.00 |  |
| 1100 العملاء (ذمم مدينة تجارية) | Al Noor Developments | 120,000.00 |  |
| 1400 أثاث وتجهيزات ومعدات مكتبية |  | 80,000.00 |  |
| 1490 مجمع الإهلاك |  |  | 20,000.00 |
| 2010 الموردون (ذمم دائنة تجارية) | Delta Ceramics |  | 95,000.00 |
| 2010 الموردون (ذمم دائنة تجارية) | Opening balances | 30,000.00 |  |
| 3010 رأس مال الشركاء |  |  | 300,000.00 |
| 3100 الأرباح المحتجزة |  |  | 250,000.00 |
| **الإجمالي** |  | **665,000.00** | **665,000.00** |

![القيد الافتتاحي بعد الترحيل: المدين والدائن 665,000.00](help/opening-jv-ar.jpg)

## راجع النتيجة
- **ميزان المراجعة**: المدين = الدائن = 665,000.00.
- **الميزانية العمومية** في 1 يناير 2026: الأصول 645,000.00 = الالتزامات 95,000.00 + حقوق الملكية 550,000.00.
- **حسابات الموردين**: يظهر دلتا للسيراميك فقط (95,000.00)، ورصيد «أرصدة افتتاحية» صفر.
- **أرصدة المخزون**: 200 شكارة معجون حوائط بقيمة 30,000.00.

> إذا اختلفت الأرباح المحتجزة كثيراً عن الدفاتر القديمة فهناك رصيد ناقص أو مُدخل في الجانب الخطأ. قارن حساباً بحساب مع ميزان المراجعة القديم قبل ترحيل أي شيء آخر.

## أصول ثابتة تريد إهلاكها تلقائياً
بدلاً من سطري الأصول في القيد، أضف كل أصل من **الأصول والخزينة ← سجل الأصول الثابتة**، واختر **التسجيل: مسجل مسبقاً (رصيد افتتاحي): تسجيل فقط**، واملأ **مجمع الإهلاك السابق (إن وجد)**. يكمل السجل الإهلاك من حيث توقفت الدفاتر القديمة. واحتفظ بتكلفة الأصل ومجمع إهلاكه في القيد الافتتاحي لتكتمل الميزانية.

## ثم أقفل تاريخ البداية
اضبط **إعدادات النظام ← النظام والبيانات ← الدفاتر مقفلة حتى تاريخ** على 1 يناير 2026، حتى لا تتغير الأرصدة الافتتاحية بالخطأ.$g$, 2, true, 'BeyondERP'
from s where exists (select 1 from public.help_categories where key = $g$start$g$);

with s as (insert into public.help_seeded(key) values ($g$guide-2026-11-03$g$) on conflict (key) do nothing returning key)
insert into public.help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
select $g$accounts$g$, $g$Correcting a posted entry: void, reverse or adjust$g$, $g$تصحيح قيد مرحّل: الإلغاء أو العكس أو التسوية$g$, $g$A posted entry is never deleted or quietly rewritten: every correction leaves a visible trail. Open the entry (click it in any list or report) and use the buttons at the bottom of its panel.

![An entry's panel: Void and Reverse are at the bottom](help/fix-entry-en.jpg)

## Void: the entry should never have existed
Use **Void** for a mistake such as a wrong amount, the wrong account, or an entry posted twice. The entry stays in the journal marked void, with your reason, and leaves every balance and report.

*Example:* bank charges typed as 1,500 instead of 150. Void it with the reason "Wrong amount: 1,500 instead of 150", then enter the right amount again.

![A voided entry keeps its lines and shows why it was voided](help/fix-voided-en.jpg)

## Reverse: the entry was right then, and must be undone on a later date
Use **Reverse** when the original was correct on its date but must be cancelled on a later one, typically an **accrual** at month end that the real bill replaces next month. The app posts a mirror entry on the date you choose; both stay in the books.

*Example:* March electricity estimated at 4,500 on 31 March, reversed on 1 April when the bill arrives:
| Account | Debit | Credit |
|---|---|---|
| 6040 Utilities (Electricity & Water) |  | 4,500.00 |
| 2030 Accrued Expenses | 4,500.00 |  |
| **Total** | **4,500.00** | **4,500.00** |

## Adjust: part of it was wrong
When only part is wrong (for example a later discount), leave the original and post a new entry for the difference, with a memo that names the original.

## What the app refuses, and why
- **A stock receipt that later issues already used.** Voiding it would leave the store short. The app explains, for example: *"Cement bag 50kg in Main Store would run short on 2026-03-10: 80 bag go out with only 0 left. Undo or move the later movements first, or correct the quantity with a count."* Undo the later issue first, or correct with a stock count.
- **Anything dated in a closed period.** Once **System Settings → System & data → Books closed up to (lock date)** is set, entries on or before that date are locked: *"The books are closed up to 2026-03-31. Change the lock date in Settings to post or edit on 2026-03-20."*

![The lock date in System Settings → System & data](help/fix-lock-en.jpg)

> Who can do what: voiding and reversing need *edit* permission on the journal (or on that document's screen). Users who send entries for approval send their corrections for approval too.$g$, $g$القيد المرحّل لا يُحذف ولا يُعاد كتابته بصمت أبداً: كل تصحيح يترك أثراً واضحاً. افتح القيد (اضغط عليه في أي قائمة أو تقرير) واستخدم الأزرار أسفل نافذته.

![نافذة القيد: زرا الإلغاء والعكس في الأسفل](help/fix-entry-ar.jpg)

## الإلغاء: القيد ما كان يجب أن يوجد
استخدم **إلغاء** للخطأ، مثل مبلغ خاطئ أو حساب خاطئ أو قيد مرحّل مرتين. يبقى القيد في اليومية بعلامة «ملغى» مع السبب الذي تكتبه، ويخرج من كل الأرصدة والتقارير.

*مثال:* مصروفات بنكية كُتبت 1,500 بدلاً من 150. ألغِ القيد بسبب «مبلغ خاطئ: 1,500 بدلاً من 150»، ثم أدخل المبلغ الصحيح من جديد.

![القيد الملغى يحتفظ بسطوره ويوضح سبب إلغائه](help/fix-voided-ar.jpg)

## العكس: القيد كان صحيحاً في وقته ويجب إلغاؤه في تاريخ لاحق
استخدم **عكس** عندما يكون الأصل صحيحاً في تاريخه لكن يجب إلغاؤه في تاريخ لاحق، وأشهر مثال **المصروف المستحق** في آخر الشهر الذي تحل محله الفاتورة الفعلية في الشهر التالي. يرحّل البرنامج قيداً معاكساً في التاريخ الذي تختاره، ويبقى القيدان في الدفاتر.

*مثال:* كهرباء مارس مقدّرة بـ 4,500 في 31 مارس، وعُكست في 1 أبريل عند وصول الفاتورة:
| الحساب | مدين | دائن |
|---|---|---|
| 6040 كهرباء ومياه |  | 4,500.00 |
| 2030 مصروفات مستحقة | 4,500.00 |  |
| **الإجمالي** | **4,500.00** | **4,500.00** |

## التسوية: جزء منه فقط خاطئ
إذا كان جزء فقط خاطئاً (مثل خصم لاحق)، اترك الأصل ورحّل قيداً جديداً بالفرق، واذكر القيد الأصلي في البيان.

## ما يرفضه البرنامج ولماذا
- **سند استلام مخزون صُرف منه لاحقاً.** إلغاؤه يجعل رصيد المخزن بالسالب. يوضح البرنامج ذلك، مثلاً: *«رصيد أسمنت شكارة 50 كجم في المخزن الرئيسي سيصبح بالسالب بتاريخ 2026-03-10…»* ألغِ الصرف اللاحق أولاً، أو صحح الكمية بجرد.
- **أي قيد بتاريخ في فترة مقفلة.** بعد ضبط **إعدادات النظام ← النظام والبيانات ← الدفاتر مقفلة حتى تاريخ** تُقفل القيود بتاريخ مساوٍ له أو قبله، ويرد البرنامج برسالة توضح تاريخ الإقفال وأين يُغيّر.

![تاريخ الإقفال في إعدادات النظام ← النظام والبيانات](help/fix-lock-ar.jpg)

> الصلاحيات: الإلغاء والعكس يحتاجان صلاحية *تعديل* على اليومية (أو على شاشة المستند). والمستخدم الذي يرسل قيوده للاعتماد يرسل تصحيحاته للاعتماد أيضاً.$g$, 1, true, 'BeyondERP'
from s where exists (select 1 from public.help_categories where key = $g$accounts$g$);

with s as (insert into public.help_seeded(key) values ($g$guide-2026-11-04$g$) on conflict (key) do nothing returning key)
insert into public.help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
select $g$accounts$g$, $g$Hard entries, step by step: accruals, prepayments, bad debts, salaries owed and more$g$, $g$القيود الصعبة خطوة بخطوة: المستحقات والمقدمات والديون المعدومة والرواتب المستحقة وغيرها$g$, $g$Most month-end entries have a ready **model** that fills in the lines for you. Open **General Accounting → Journal Entries → + New** and pick it from **Template** (the ones you use most come first). You enter the amount and choose the accounts the model asks for. The tables below are exactly what the app posts.

## An expense owed but not yet billed (accrual)
Model **Accrue an expense (owed, not yet billed)**. March electricity is about 4,500; the bill comes in April:
| Account | Debit | Credit |
|---|---|---|
| 6040 Utilities (Electricity & Water) | 4,500.00 |  |
| 2030 Accrued Expenses |  | 4,500.00 |
| **Total** | **4,500.00** | **4,500.00** |
When the bill arrives, **reverse** this entry on its date and record the real bill (see *Correcting a posted entry*).

## A prepaid expense used up month by month
The annual insurance (36,000) was paid in January into *Prepaid expenses*. Each month, model **Use up a prepaid expense** moves one month (3,000) to expense:
| Account | Debit | Credit |
|---|---|---|
| 6085 Insurance | 3,000.00 |  |
| 1300 Prepaid Expenses |  | 3,000.00 |
| **Total** | **3,000.00** | **3,000.00** |
> Put it on **General Accounting → Recurring Journals** to post it automatically every month.

## A customer who may not pay: provision first, write-off later
1. Model **Provision for doubtful debts**: 15,000 of a 60,000 balance is doubtful:
| Account | Debit | Credit |
|---|---|---|
| 6088 Expected Credit Loss Expense | 15,000.00 |  |
| 1190 Allowance for Expected Credit Losses |  | 15,000.00 |
| **Total** | **15,000.00** | **15,000.00** |
2. When the debt is confirmed lost, write the **whole balance** off against the provision, and charge only the rest to expense. Use a journal voucher with three lines, the customer named on the receivables line:

![Write-off against the provision: 15,000 from the allowance, 45,000 to expense](help/hard-writeoff-en.jpg)

| Account | Debit | Credit |
|---|---|---|
| 1190 Allowance for Expected Credit Losses | 15,000.00 | |
| 6088 Expected Credit Loss Expense | 45,000.00 | |
| 1100 Trade Receivables — Customers | | 60,000.00 |

> The model **Write off a bad debt** charges the full amount to expense. Use it only when there was **no** provision for that debt, otherwise the loss is counted twice.

## A partner paid a company cost personally
Model **A partner paid a company cost personally** puts the expense in the books and what the company owes the partner on their current account:
| Account | Debit | Credit |
|---|---|---|
| 6075 Travel & Transport | 2,500.00 |  |
| 3020 Partners' Current Accounts |  | 2,500.00 |
| **Total** | **2,500.00** | **2,500.00** |

## Salaries owed for the month
Model **Salaries owed for the month**, on the last day of the month; the payment on the 5th then settles *Accrued salaries & wages*:
| Account | Debit | Credit |
|---|---|---|
| 6050 Administrative Salaries & Wages | 42,000.00 |  |
| 2020 Accrued Salaries & Wages |  | 42,000.00 |
| **Total** | **42,000.00** | **42,000.00** |

## More models you'll find in the list
Moving money between cash and bank, bank charges, loans received and repaid, interest, manual depreciation, a supplier advance and applying it to their bill, a client advance and applying it to an invoice, VAT settlement and payment, withholding tax payment, offsetting client-withheld tax against income tax, the legal reserve, and more.

> Make your own: fill a journal voucher and press **Save as a model**. Models can also be put on the **+ New** menu from **System Settings → Menus**.$g$, $g$لمعظم قيود آخر الشهر **نموذج** جاهز يملأ السطور عنك. افتح **المحاسبة العامة ← قيود اليومية ← + جديد** واختره من **نموذج** (الأكثر استخداماً في الأعلى). تُدخل المبلغ وتختار الحسابات التي يطلبها النموذج. الجداول أدناه هي بالضبط ما يرحّله البرنامج.

## مصروف مستحق لم تصل فاتورته
نموذج **إثبات مصروف مستحق**. كهرباء مارس حوالي 4,500 والفاتورة تصل في أبريل:
| الحساب | مدين | دائن |
|---|---|---|
| 6040 كهرباء ومياه | 4,500.00 |  |
| 2030 مصروفات مستحقة |  | 4,500.00 |
| **الإجمالي** | **4,500.00** | **4,500.00** |
عند وصول الفاتورة **اعكس** هذا القيد بتاريخها وسجّل الفاتورة الفعلية (انظر *تصحيح قيد مرحّل*).

## مصروف مدفوع مقدماً يُستهلك شهرياً
دُفع التأمين السنوي (36,000) في يناير إلى *مصروفات مدفوعة مقدماً*. وكل شهر ينقل نموذج **استهلاك مصروف مقدم** شهراً واحداً (3,000) إلى المصروفات:
| الحساب | مدين | دائن |
|---|---|---|
| 6085 تأمين | 3,000.00 |  |
| 1300 مصروفات مدفوعة مقدماً |  | 3,000.00 |
| **الإجمالي** | **3,000.00** | **3,000.00** |
> ضعه في **المحاسبة العامة ← القيود الدورية** ليُرحّل تلقائياً كل شهر.

## عميل قد لا يسدد: المخصص أولاً ثم الإعدام
1. نموذج **مخصص ديون مشكوك فيها**: 15,000 من رصيد 60,000 مشكوك في تحصيلها:
| الحساب | مدين | دائن |
|---|---|---|
| 6088 مصروف الخسائر الائتمانية المتوقعة | 15,000.00 |  |
| 1190 مخصص الخسائر الائتمانية المتوقعة |  | 15,000.00 |
| **الإجمالي** | **15,000.00** | **15,000.00** |
2. عند التأكد من ضياع الدين، أعدم **الرصيد كله** مقابل المخصص، ولا تحمّل المصروفات إلا بالباقي. استخدم قيد يومية من ثلاثة سطور مع اسم العميل في سطر العملاء:

![الإعدام مقابل المخصص: 15,000 من المخصص و45,000 مصروف](help/hard-writeoff-ar.jpg)

| الحساب | مدين | دائن |
|---|---|---|
| 1190 مخصص الخسائر الائتمانية المتوقعة | 15,000.00 | |
| 6088 مصروف الخسائر الائتمانية المتوقعة | 45,000.00 | |
| 1100 العملاء (ذمم مدينة تجارية) | | 60,000.00 |

> نموذج **إعدام دين معدوم** يحمّل المبلغ كله على المصروفات. استخدمه فقط إذا **لم** يكن هناك مخصص لهذا الدين، وإلا حُسبت الخسارة مرتين.

## شريك دفع تكلفة للشركة من ماله
نموذج **شريك دفع تكلفة للشركة من ماله** يُثبت المصروف في الدفاتر، وما تدين به الشركة للشريك في حسابه الجاري:
| الحساب | مدين | دائن |
|---|---|---|
| 6075 انتقالات وسفر | 2,500.00 |  |
| 3020 جاري الشركاء |  | 2,500.00 |
| **الإجمالي** | **2,500.00** | **2,500.00** |

## الرواتب المستحقة عن الشهر
نموذج **إثبات الرواتب المستحقة** في آخر يوم من الشهر، ثم يسوّي الصرف في يوم 5 حساب *رواتب وأجور مستحقة*:
| الحساب | مدين | دائن |
|---|---|---|
| 6050 رواتب وأجور إدارية | 42,000.00 |  |
| 2020 رواتب وأجور مستحقة |  | 42,000.00 |
| **الإجمالي** | **42,000.00** | **42,000.00** |

## نماذج أخرى في القائمة
التحويل بين الخزينة والبنك، والمصروفات البنكية، واستلام القروض وسدادها، والفوائد، والإهلاك اليدوي، والدفعة المقدمة لمورد وتسويتها مع فاتورته، والدفعة المقدمة من عميل وتسويتها مع فاتورة، وتسوية ضريبة القيمة المضافة وسدادها، وسداد ضريبة الخصم والتحصيل، وخصم ضريبة الخصم والتحصيل من ضريبة الدخل، والاحتياطي القانوني، وغيرها.

> اصنع نموذجك: املأ قيد يومية واضغط **حفظ كنموذج**. ويمكن وضع النماذج في قائمة **+ جديد** من **إعدادات النظام ← القوائم**.$g$, 2, true, 'BeyondERP'
from s where exists (select 1 from public.help_categories where key = $g$accounts$g$);

with s as (insert into public.help_seeded(key) values ($g$guide-2026-11-05$g$) on conflict (key) do nothing returning key)
insert into public.help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
select $g$accounts$g$, $g$Fixed assets: buying, monthly depreciation, and selling an asset at a loss$g$, $g$الأصول الثابتة: الشراء والإهلاك الشهري وبيع أصل بخسارة$g$, $g$Keep each asset in **Assets & Treasury → Fixed Asset Register**: the register books the purchase, works out depreciation every month and books the sale with its gain or loss.

## Buying: a pickup truck on credit
**New asset**: name *Pickup truck*, account *Motor Vehicles*, acquired 15 January 2026, cost 600,000, salvage value 60,000, useful life 60 months. Choose **Recording: On credit / partly paid**, supplier *Nile Motors*, paid now 100,000, VAT 14%. The app posts:
| Account | Debit | Credit |
|---|---|---|
| 1420 Motor Vehicles | 600,000.00 |  |
| 1320 Input VAT Recoverable | 84,000.00 |  |
| 1020 Cash at Bank |  | 100,000.00 |
| 2010 Trade Payables — Suppliers |  | 584,000.00 |
| **Total** | **684,000.00** | **684,000.00** |
The supplier is owed 584,000.00 (600,000 + 84,000 VAT − 100,000 paid). The input VAT is recoverable, not part of the asset's cost.

## Depreciation every month
Straight line: (600,000 − 60,000) ÷ 60 months = **9,000 a month**. The month of purchase counts in full. Open **Assets & Treasury → Depreciation Run**, choose the month and post: one entry for each asset. January to April at once:
| Account | Debit | Credit |
|---|---|---|
| 7010 Depreciation Expense | 36,000.00 |  |
| 1490 Accumulated Depreciation |  | 36,000.00 |
| **Total** | **36,000.00** | **36,000.00** |
A month already depreciated is not booked twice. If a run is voided, the next run books it again.

## Selling it: sold on 20 June for 500,000
Open the asset and press **Sell / dispose**, enter the date, the proceeds and where the money went. The app first depreciates the months not yet booked (May and June, 18,000):
| Account | Debit | Credit |
|---|---|---|
| 7010 Depreciation Expense | 18,000.00 |  |
| 1490 Accumulated Depreciation |  | 18,000.00 |
| **Total** | **18,000.00** | **18,000.00** |
then books the sale. Cost 600,000 − accumulated depreciation 54,000 = book value 546,000; sold for 500,000, so the loss is **46,000**:
| Account | Debit | Credit |
|---|---|---|
| 1020 Cash at Bank | 500,000.00 |  |
| 1490 Accumulated Depreciation | 54,000.00 |  |
| 1420 Motor Vehicles |  | 600,000.00 |
| 7045 Loss on Disposal of Fixed Assets | 46,000.00 |  |
| **Total** | **600,000.00** | **600,000.00** |

![The sale entry: the asset and its depreciation leave the books, the loss is shown](help/asset-sale-en.jpg)

![The register: the truck shown as disposed](help/asset-register-en.jpg)

> Sold for more than book value? The same screen books a **gain** on disposal instead.$g$, $g$سجّل كل أصل في **الأصول والخزينة ← سجل الأصول الثابتة**: يُثبت السجل الشراء، ويحسب الإهلاك كل شهر، ويُثبت البيع بربحه أو خسارته.

## الشراء: سيارة نصف نقل بالآجل
**أصل جديد**: الاسم *سيارة نصف نقل*، الحساب *وسائل نقل وانتقال*، تاريخ الاقتناء 15 يناير 2026، التكلفة 600,000، قيمة الخردة 60,000، العمر الإنتاجي 60 شهراً. اختر **التسجيل: آجل / مدفوع جزئياً**، والمورد *النيل للسيارات*، والمدفوع الآن 100,000، وضريبة القيمة المضافة 14%. يرحّل البرنامج:
| الحساب | مدين | دائن |
|---|---|---|
| 1420 وسائل نقل وانتقال | 600,000.00 |  |
| 1320 ضريبة القيمة المضافة — مدخلات | 84,000.00 |  |
| 1020 النقدية بالبنوك |  | 100,000.00 |
| 2010 الموردون (ذمم دائنة تجارية) |  | 584,000.00 |
| **الإجمالي** | **684,000.00** | **684,000.00** |
المستحق للمورد 584,000.00 (600,000 + ضريبة 84,000 − المدفوع 100,000). وضريبة المدخلات قابلة للخصم وليست جزءاً من تكلفة الأصل.

## الإهلاك كل شهر
القسط الثابت: (600,000 − 60,000) ÷ 60 شهراً = **9,000 شهرياً**، ويُحسب شهر الشراء كاملاً. افتح **الأصول والخزينة ← احتساب الإهلاك**، واختر الشهر ورحّل: قيد لكل أصل. من يناير إلى أبريل مرة واحدة:
| الحساب | مدين | دائن |
|---|---|---|
| 7010 مصروف الإهلاك | 36,000.00 |  |
| 1490 مجمع الإهلاك |  | 36,000.00 |
| **الإجمالي** | **36,000.00** | **36,000.00** |
الشهر الذي أُهلك لا يُسجل مرتين، وإذا أُلغي قيد الإهلاك يسجله الاحتساب التالي من جديد.

## البيع: بيعت في 20 يونيو بمبلغ 500,000
افتح الأصل واضغط **بيع / استبعاد**، وأدخل التاريخ والمتحصلات وأين ذهب المال. يُهلك البرنامج أولاً الشهور التي لم تُسجل (مايو ويونيو، 18,000):
| الحساب | مدين | دائن |
|---|---|---|
| 7010 مصروف الإهلاك | 18,000.00 |  |
| 1490 مجمع الإهلاك |  | 18,000.00 |
| **الإجمالي** | **18,000.00** | **18,000.00** |
ثم يُثبت البيع. التكلفة 600,000 − مجمع الإهلاك 54,000 = القيمة الدفترية 546,000؛ والبيع بـ 500,000، فالخسارة **46,000**:
| الحساب | مدين | دائن |
|---|---|---|
| 1020 النقدية بالبنوك | 500,000.00 |  |
| 1490 مجمع الإهلاك | 54,000.00 |  |
| 1420 وسائل نقل وانتقال |  | 600,000.00 |
| 7045 خسائر بيع أصول ثابتة | 46,000.00 |  |
| **الإجمالي** | **600,000.00** | **600,000.00** |

![قيد البيع: يخرج الأصل ومجمع إهلاكه وتظهر الخسارة](help/asset-sale-ar.jpg)

![السجل: السيارة مستبعدة](help/asset-register-ar.jpg)

> بيع بأكثر من القيمة الدفترية؟ تُثبت الشاشة نفسها **ربحاً** من البيع بدلاً من الخسارة.$g$, 3, true, 'BeyondERP'
from s where exists (select 1 from public.help_categories where key = $g$accounts$g$);

with s as (insert into public.help_seeded(key) values ($g$guide-2026-11-06$g$) on conflict (key) do nothing returning key)
insert into public.help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
select $g$stock$g$, $g$How stock is valued (FIFO), and what happens when a movement is entered late$g$, $g$كيف يُقيّم المخزون (الوارد أولاً يُصرف أولاً FIFO)، وماذا يحدث عند إدخال حركة متأخرة$g$, $g$BeyondERP values stock **FIFO (first in, first out)**, per item and per warehouse: what goes out is costed at the oldest units still on the shelf. **Moving average** is the alternative (super administrator, **System Settings → System & data → Stock costing**, with a preview before switching).

## A worked example: cement in the Main Store
| Date | Movement | Quantity | Unit cost |
|---|---|---|---|
| 1 March | Goods receipt | 100 | 150 |
| 10 March | Goods receipt | 100 | 160 |
| 15 March | Material issue to *Villa 12* | 150 | ? |

FIFO takes the 100 bags at 150 first, then 50 of the bags at 160: 15,000 + 8,000 = **23,000**. The project is charged exactly that:
| Account | Debit | Credit |
|---|---|---|
| 5010 Direct Materials | 23,000.00 |  |
| 1200 Materials Inventory |  | 23,000.00 |
| **Total** | **23,000.00** | **23,000.00** |
50 bags are left, valued at 50 × 160 = 8,000.

## A receipt entered late
On 20 March someone enters a delivery that really arrived on **5 March**: 50 bags at 140. It belongs before the issue, so FIFO now gives the issue 100 at 150 + 50 at 140 = **22,000**, which is 1,000 less. The app does **not** rewrite the posted issue. It posts the receipt and a dated **Stock Cost Adjustment** for the difference, on the issue's own date:
| Account | Debit | Credit |
|---|---|---|
| 1200 Materials Inventory | 7,000.00 |  |
| 2010 Trade Payables — Suppliers |  | 7,000.00 |
| **Total** | **7,000.00** | **7,000.00** |

| Account | Debit | Credit |
|---|---|---|
| 1200 Materials Inventory | 1,000.00 |  |
| 5010 Direct Materials |  | 1,000.00 |
| **Total** | **1,000.00** | **1,000.00** |
Both stay linked: voiding the late receipt undoes its adjustment too.

![Stock Movements in date order, with the issue's corrected value](help/fifo-moves-en.jpg)

![The adjustment entry, dated on the issue's own date](help/fifo-recost-en.jpg)

## Counting the shelf
On 31 March the count finds 95 bags; the books say 100. Enter it under **Inventory → Physical Stock Counts** (or as a count adjustment in Stock Movements). The 5 missing bags are costed at FIFO (the layer at 160) and charged to *Inventory count differences*:
| Account | Debit | Credit |
|---|---|---|
| 5080 Inventory Count Differences | 800.00 |  |
| 1200 Materials Inventory |  | 800.00 |
| **Total** | **800.00** | **800.00** |

## What the app refuses
A movement that would make a store go below zero on any day is refused, even when it is entered late: *"Only 100 bag of Cement bag 50kg in Main Store on 2026-03-02"*. Enter the missing receipt first, with its real date.

## The same example with moving average
| | FIFO | Moving average |
|---|---|---|
| Issue on 15 March | 23,000.00 | 23,250.00 |
| Late receipt adjustment | −1,000.00 | −450.00 |
| Count loss (5 bags) | 800.00 | 760.00 |
| Left: 95 bags | 15,200.00 | 14,440.00 |

## Short clip: issuing stock to a project
![Issuing 20 bags to Villa 12, then the balance left in Stock Balances](help/clip-stock-issue-en.webm)

> The Excel workbook computes the same FIFO costs with formulas, so an exported workbook agrees with the app to the cent.$g$, $g$يقيّم BeyondERP المخزون بطريقة **الوارد أولاً يُصرف أولاً (FIFO)** لكل صنف في كل مخزن: ما يخرج يُحسب بتكلفة أقدم الوحدات الموجودة. والبديل هو **المتوسط المرجح المتحرك** (للمسؤول الأعلى من **إعدادات النظام ← النظام والبيانات ← تقييم المخزون** مع معاينة قبل التغيير).

## مثال محلول: الأسمنت في المخزن الرئيسي
| التاريخ | الحركة | الكمية | تكلفة الوحدة |
|---|---|---|---|
| 1 مارس | استلام | 100 | 150 |
| 10 مارس | استلام | 100 | 160 |
| 15 مارس | صرف إلى *فيلا 12* | 150 | ؟ |

تأخذ FIFO أولاً الـ 100 شكارة بسعر 150، ثم 50 شكارة بسعر 160: 15,000 + 8,000 = **23,000**، ويُحمّل المشروع هذا المبلغ بالضبط:
| الحساب | مدين | دائن |
|---|---|---|
| 5010 خامات مباشرة | 23,000.00 |  |
| 1200 مخزون الخامات |  | 23,000.00 |
| **الإجمالي** | **23,000.00** | **23,000.00** |
يتبقى 50 شكارة قيمتها 50 × 160 = 8,000.

## استلام أُدخل متأخراً
في 20 مارس أدخل أحدهم توريداً وصل فعلاً في **5 مارس**: 50 شكارة بسعر 140. مكانه قبل الصرف، فتصبح تكلفة الصرف بطريقة FIFO: 100 بسعر 150 + 50 بسعر 140 = **22,000**، أي أقل بـ 1,000. البرنامج **لا** يعيد كتابة قيد الصرف المرحّل، بل يرحّل الاستلام و**تسوية تكلفة مخزون** بالفرق مؤرخة بتاريخ الصرف نفسه:
| الحساب | مدين | دائن |
|---|---|---|
| 1200 مخزون الخامات | 7,000.00 |  |
| 2010 الموردون (ذمم دائنة تجارية) |  | 7,000.00 |
| **الإجمالي** | **7,000.00** | **7,000.00** |

| الحساب | مدين | دائن |
|---|---|---|
| 1200 مخزون الخامات | 1,000.00 |  |
| 5010 خامات مباشرة |  | 1,000.00 |
| **الإجمالي** | **1,000.00** | **1,000.00** |
ويبقيان مرتبطين: إلغاء الاستلام المتأخر يلغي تسويته أيضاً.

![حركات المخزون بترتيب التاريخ مع القيمة المصححة للصرف](help/fifo-moves-ar.jpg)

![قيد التسوية مؤرخاً بتاريخ الصرف نفسه](help/fifo-recost-ar.jpg)

## الجرد الفعلي
في 31 مارس وجد الجرد 95 شكارة، والدفاتر تقول 100. أدخله من **المخزون ← الجرد الفعلي للمخزون** (أو كتسوية جرد في حركات المخزون). تُحسب الشكائر الخمس الناقصة بطريقة FIFO (طبقة 160) وتُحمّل على *فروق جرد المخزون*:
| الحساب | مدين | دائن |
|---|---|---|
| 5080 فروق جرد المخزون | 800.00 |  |
| 1200 مخزون الخامات |  | 800.00 |
| **الإجمالي** | **800.00** | **800.00** |

## ما يرفضه البرنامج
الحركة التي تجعل رصيد مخزن أقل من صفر في أي يوم تُرفض حتى لو أُدخلت متأخرة، مع رسالة تذكر الكمية المتاحة في ذلك اليوم. أدخل الاستلام الناقص أولاً بتاريخه الحقيقي.

## المثال نفسه بالمتوسط المرجح المتحرك
| | FIFO | المتوسط المتحرك |
|---|---|---|
| الصرف في 15 مارس | 23,000.00 | 23,250.00 |
| تسوية الاستلام المتأخر | −1,000.00 | −450.00 |
| عجز الجرد (5 شكائر) | 800.00 | 760.00 |
| المتبقي: 95 شكارة | 15,200.00 | 14,440.00 |

## مقطع قصير: صرف مخزون إلى مشروع
![صرف 20 شكارة إلى فيلا 12 ثم الرصيد المتبقي في أرصدة المخزون](help/clip-stock-issue-ar.webm)

> يحسب مصنف Excel تكاليف FIFO نفسها بالمعادلات، فيطابق المصنف المُصدَّر البرنامج حتى القرش.$g$, 1, true, 'BeyondERP'
from s where exists (select 1 from public.help_categories where key = $g$stock$g$);

with s as (insert into public.help_seeded(key) values ($g$guide-2026-11-07$g$) on conflict (key) do nothing returning key)
insert into public.help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
select $g$sales$g$, $g$Progress bills with an advance, its recovery, retention, VAT and withholding tax$g$, $g$المستخلصات مع الدفعة المقدمة واستردادها والمحتجزات وضريبة القيمة المضافة والخصم والتحصيل$g$, $g$A full cycle on one project: a 1,000,000 contract, a 10% advance, a first progress bill of 300,000 of work, 10% of it recovered from the advance and 5% held as retention.

## 1. The client pays a 10% advance (100,000)
**Sales & Receivables → Receipt Vouchers → + New**, and choose **Credit account: Client advance (unearned)**. It is not revenue yet: it is owed back as work, until it is recovered from the bills.
| Account | Debit | Credit |
|---|---|---|
| 1020 Cash at Bank | 100,000.00 |  |
| 2100 Contract Liabilities — Customer Advances |  | 100,000.00 |
| **Total** | **100,000.00** | **100,000.00** |

## 2. Progress bill no. 1: 300,000 of work
**Sales & Receivables → Sales Invoices → + New**: the project, the client, the work lines (plastering 2,000 m² × 90; floor tiling 800 m² × 150), **VAT 14%** and **WHT 1% — contracting** (the client withholds it). The bill is 300,000 + 42,000 VAT − 3,000 withheld = **339,000**. Press **Save & post**:
| Account | Debit | Credit |
|---|---|---|
| 1100 Trade Receivables — Customers | 339,000.00 |  |
| 1330 Withholding Tax Credits (Deducted by Customers) | 3,000.00 |  |
| 4010 Contract Revenue — Execution |  | 300,000.00 |
| 2200 Output VAT Payable |  | 42,000.00 |
| **Total** | **342,000.00** | **342,000.00** |

![The progress bill: lines, VAT and withholding](help/pb-invoice-en.jpg)

## 3. Recover 10% of the advance (30,000), with model *Apply client advance to invoice*
| Account | Debit | Credit |
|---|---|---|
| 2100 Contract Liabilities — Customer Advances | 30,000.00 |  |
| 1100 Trade Receivables — Customers |  | 30,000.00 |
| **Total** | **30,000.00** | **30,000.00** |

## 4. Retention held by the client: 5% (15,000)
Move it out of ordinary receivables so aging shows what is really due now. Use a journal voucher with the client's name:
| Account | Debit | Credit |
|---|---|---|
| 1150 Retentions Receivable | 15,000.00 |  |
| 1100 Trade Receivables — Customers |  | 15,000.00 |
| **Total** | **15,000.00** | **15,000.00** |

## 5. The client pays the rest: 294,000
339,000 − 30,000 − 15,000 = 294,000. **Receipt Voucher**, and this time choose **Credit account: Settle receivable (invoice)**:
| Account | Debit | Credit |
|---|---|---|
| 1020 Cash at Bank | 294,000.00 |  |
| 1100 Trade Receivables — Customers |  | 294,000.00 |
| **Total** | **294,000.00** | **294,000.00** |

![The receipt that settles an invoice: Credit account set to "Settle receivable (invoice)"](help/pb-receipt-form-en.jpg)

> **The most common mistake:** leaving the receipt on **Revenue (cash basis)**. That books the payment as new revenue, so the invoice's revenue is counted twice and the client still looks as if they owe. When an invoice exists, the receipt must **settle the receivable**.

## 6. At handover, the retention comes in
Model **Client retention collected**:
| Account | Debit | Credit |
|---|---|---|
| 1020 Cash at Bank | 15,000.00 |  |
| 1150 Retentions Receivable |  | 15,000.00 |
| **Total** | **15,000.00** | **15,000.00** |

## Where everything ends up
Receivables 0 · Retentions 0 · Client advances 70,000 still to recover from later bills · Revenue 300,000 · Output VAT 42,000 (to declare) · Withholding tax credit 3,000 (deducted from income tax).

## Short clip: a receipt that settles an invoice
![Receiving 90,000 against bill PB-02: the project's receivable falls from 105,000 to 15,000, the retention still held](help/clip-receipt-invoice-en.webm)$g$, $g$دورة كاملة لمشروع واحد: عقد بقيمة 1,000,000، ودفعة مقدمة 10%، ومستخلص أول بأعمال 300,000، يُسترد منه 10% من الدفعة المقدمة ويُحتجز 5%.

## 1. العميل يدفع دفعة مقدمة 10% (100,000)
**المبيعات والمدينون ← سندات القبض ← + جديد**، واختر **الحساب الدائن: دفعة مقدمة من العميل**. ليست إيراداً بعد: هي مستحقة للعميل أعمالاً حتى تُسترد من المستخلصات.
| الحساب | مدين | دائن |
|---|---|---|
| 1020 النقدية بالبنوك | 100,000.00 |  |
| 2100 التزامات العقود — دفعات مقدمة من العملاء |  | 100,000.00 |
| **الإجمالي** | **100,000.00** | **100,000.00** |

## 2. المستخلص رقم 1: أعمال 300,000
**المبيعات والمدينون ← فواتير المبيعات ← + جديد**: المشروع والعميل وسطور الأعمال (محارة 2,000 م² × 90؛ سيراميك أرضيات 800 م² × 150)، و**ضريبة القيمة المضافة 14%** و**خصم وتحصيل 1% — مقاولات** (يخصمه العميل). المستخلص 300,000 + ضريبة 42,000 − خصم 3,000 = **339,000**. اضغط **حفظ وترحيل**:
| الحساب | مدين | دائن |
|---|---|---|
| 1100 العملاء (ذمم مدينة تجارية) | 339,000.00 |  |
| 1330 ضريبة خصم وتحصيل لدى العملاء | 3,000.00 |  |
| 4010 إيرادات العقود — التنفيذ |  | 300,000.00 |
| 2200 ضريبة القيمة المضافة — مخرجات |  | 42,000.00 |
| **الإجمالي** | **342,000.00** | **342,000.00** |

![المستخلص: السطور والضريبة والخصم والتحصيل](help/pb-invoice-ar.jpg)

## 3. استرداد 10% من الدفعة المقدمة (30,000) بنموذج *تسوية دفعة مقدمة مع فاتورة*
| الحساب | مدين | دائن |
|---|---|---|
| 2100 التزامات العقود — دفعات مقدمة من العملاء | 30,000.00 |  |
| 1100 العملاء (ذمم مدينة تجارية) |  | 30,000.00 |
| **الإجمالي** | **30,000.00** | **30,000.00** |

## 4. محتجزات لدى العميل: 5% (15,000)
انقلها من العملاء العاديين حتى تُظهر أعمار الديون المستحق فعلاً الآن. استخدم قيد يومية باسم العميل:
| الحساب | مدين | دائن |
|---|---|---|
| 1150 محتجزات لدى العملاء (ضمان أعمال) | 15,000.00 |  |
| 1100 العملاء (ذمم مدينة تجارية) |  | 15,000.00 |
| **الإجمالي** | **15,000.00** | **15,000.00** |

## 5. العميل يسدد الباقي: 294,000
339,000 − 30,000 − 15,000 = 294,000. **سند قبض**، واختر هذه المرة **الحساب الدائن: تسوية مديونية (فاتورة)**:
| الحساب | مدين | دائن |
|---|---|---|
| 1020 النقدية بالبنوك | 294,000.00 |  |
| 1100 العملاء (ذمم مدينة تجارية) |  | 294,000.00 |
| **الإجمالي** | **294,000.00** | **294,000.00** |

![سند القبض الذي يسوي فاتورة: الحساب الدائن «تسوية مديونية (فاتورة)»](help/pb-receipt-form-ar.jpg)

> **أشهر خطأ:** ترك السند على **إيراد (أساس نقدي)**. هذا يسجل التحصيل كإيراد جديد، فيُحسب إيراد الفاتورة مرتين ويظل العميل مديناً. إذا وُجدت فاتورة، فيجب أن يسوّي السند **المديونية**.

## 6. عند التسليم تُحصّل المحتجزات
نموذج **تحصيل محتجزات العميل**:
| الحساب | مدين | دائن |
|---|---|---|
| 1020 النقدية بالبنوك | 15,000.00 |  |
| 1150 محتجزات لدى العملاء (ضمان أعمال) |  | 15,000.00 |
| **الإجمالي** | **15,000.00** | **15,000.00** |

## أين انتهى كل شيء
العملاء 0 · المحتجزات 0 · دفعات العملاء المقدمة 70,000 تُسترد من المستخلصات القادمة · الإيراد 300,000 · ضريبة المخرجات 42,000 (للإقرار) · ضريبة الخصم والتحصيل لدى العملاء 3,000 (تُخصم من ضريبة الدخل).

## مقطع قصير: سند قبض يسوي فاتورة
![تحصيل 90,000 عن المستخلص PB-02: تنخفض مديونية المشروع من 105,000 إلى 15,000، وهي المحتجزات التي ما زالت لدى العميل](help/clip-receipt-invoice-ar.webm)$g$, 1, true, 'BeyondERP'
from s where exists (select 1 from public.help_categories where key = $g$sales$g$);

with s as (insert into public.help_seeded(key) values ($g$guide-2026-11-08$g$) on conflict (key) do nothing returning key)
insert into public.help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
select $g$projects$g$, $g$Subcontractor certificates: retention, advance recovery and withholding tax$g$, $g$مستخلصات مقاولي الباطن: المحتجزات واسترداد الدفعة المقدمة والخصم والتحصيل$g$, $g$Certificates are **cumulative**: each one states the quantity executed **to date**, and the app works out what is new since the previous certificate. Retention, advance recovery and withholding are taken from each certificate automatically.

## The contract
**Projects & Contracts → Contracts Register → + New**, type **Subcontract**: *Hassan Plastering Works* on *Villa 12*, internal plastering 4,000 m² × 60 and external plastering 1,000 m² × 80 (contract value **320,000**), retention **5%**, advance recovery **20%** of each certificate, withholding tax **1% (contracting)**.

![The subcontract: values, what is certified, retention held and what is owed](help/sub-contract-en.jpg)

## The advance: 32,000
From the contract, **Pay advance**:
| Account | Debit | Credit |
|---|---|---|
| 1310 Advances to Suppliers & Subcontractors | 32,000.00 |  |
| 1020 Cash at Bank |  | 32,000.00 |
| **Total** | **32,000.00** | **32,000.00** |

## Certificate 1 (31 March): 1,500 m² of internal plastering to date
This period: 1,500 × 60 = **90,000**. Retention 5% = 4,500; advance recovery 20% = 18,000; withholding 1% = 900. Owed to the subcontractor: 90,000 − 4,500 − 18,000 − 900 = **66,600**. Approve it, then post:
| Account | Debit | Credit |
|---|---|---|
| 5020 Subcontract Costs | 90,000.00 |  |
| 2015 Trade Payables — Subcontractors |  | 66,600.00 |
| 2110 Retentions Payable — Subcontractors |  | 4,500.00 |
| 1310 Advances to Suppliers & Subcontractors |  | 18,000.00 |
| 2210 Withholding Tax Payable |  | 900.00 |
| **Total** | **90,000.00** | **90,000.00** |

## Certificate 2 (30 April): internal 3,000 m² and external 400 m² to date
This period: internal (3,000 − 1,500) × 60 = 90,000 + external 400 × 80 = 32,000 → **122,000**.
- Retention 5% = 6,100; withholding 1% = 1,220.
- Advance recovery 20% would be 24,400, but only **14,000** of the advance is left (32,000 − 18,000), so the app recovers 14,000 and stops there.
- Owed: 122,000 − 6,100 − 14,000 − 1,220 = **100,680**.
| Account | Debit | Credit |
|---|---|---|
| 5020 Subcontract Costs | 122,000.00 |  |
| 2015 Trade Payables — Subcontractors |  | 100,680.00 |
| 2110 Retentions Payable — Subcontractors |  | 6,100.00 |
| 1310 Advances to Suppliers & Subcontractors |  | 14,000.00 |
| 2210 Withholding Tax Payable |  | 1,220.00 |
| **Total** | **122,000.00** | **122,000.00** |

![Certificate 2 as posted](help/sub-cert2-en.jpg)

## Paying, and releasing the retention
- **Pay**: 167,280 (66,600 + 100,680) clears what is owed:
| Account | Debit | Credit |
|---|---|---|
| 2015 Trade Payables — Subcontractors | 167,280.00 |  |
| 1020 Cash at Bank |  | 167,280.00 |
| **Total** | **167,280.00** | **167,280.00** |
- **Release retention** at the end of the maintenance period moves the 10,600 held into what is owed, then pay it like any other amount:
| Account | Debit | Credit |
|---|---|---|
| 2110 Retentions Payable — Subcontractors | 10,600.00 |  |
| 2015 Trade Payables — Subcontractors |  | 10,600.00 |
| **Total** | **10,600.00** | **10,600.00** |

> The withholding tax deducted (2,120 here) sits in *Withholding tax payable* until it is paid to the Tax Authority (model **Pay withholding tax to the Tax Authority**).

> A certificate can be reopened to correct it only while nothing after it depends on it (for example, before its retention has been released).$g$, $g$المستخلصات **تراكمية**: يذكر كل مستخلص الكمية المنفذة **حتى تاريخه**، ويحسب البرنامج الجديد منذ المستخلص السابق. وتُخصم المحتجزات واسترداد الدفعة المقدمة والخصم والتحصيل من كل مستخلص تلقائياً.

## العقد
**المشروعات والعقود ← سجل العقود ← + جديد**، النوع **عقد باطن**: *حسن لأعمال المحارة* في *فيلا 12*، محارة داخلية 4,000 م² × 60 ومحارة خارجية 1,000 م² × 80 (قيمة العقد **320,000**)، محتجزات **5%**، استرداد الدفعة المقدمة **20%** من كل مستخلص، خصم وتحصيل **1% (مقاولات)**.

![عقد مقاولة الباطن: القيم والمعتمد والمحتجزات والمستحق](help/sub-contract-ar.jpg)

## الدفعة المقدمة: 32,000
من العقد اضغط **صرف دفعة مقدمة**:
| الحساب | مدين | دائن |
|---|---|---|
| 1310 دفعات مقدمة للموردين ومقاولي الباطن | 32,000.00 |  |
| 1020 النقدية بالبنوك |  | 32,000.00 |
| **الإجمالي** | **32,000.00** | **32,000.00** |

## المستخلص 1 (31 مارس): 1,500 م² محارة داخلية حتى تاريخه
هذه الفترة: 1,500 × 60 = **90,000**. المحتجزات 5% = 4,500؛ استرداد الدفعة المقدمة 20% = 18,000؛ الخصم والتحصيل 1% = 900. المستحق لمقاول الباطن: 90,000 − 4,500 − 18,000 − 900 = **66,600**. اعتمده ثم رحّله:
| الحساب | مدين | دائن |
|---|---|---|
| 5020 تكاليف مقاولي الباطن | 90,000.00 |  |
| 2015 مقاولو الباطن (ذمم دائنة) |  | 66,600.00 |
| 2110 محتجزات مقاولي الباطن |  | 4,500.00 |
| 1310 دفعات مقدمة للموردين ومقاولي الباطن |  | 18,000.00 |
| 2210 ضريبة الخصم والتحصيل المستحقة |  | 900.00 |
| **الإجمالي** | **90,000.00** | **90,000.00** |

## المستخلص 2 (30 أبريل): داخلية 3,000 م² وخارجية 400 م² حتى تاريخه
هذه الفترة: داخلية (3,000 − 1,500) × 60 = 90,000 + خارجية 400 × 80 = 32,000 ← **122,000**.
- المحتجزات 5% = 6,100؛ الخصم والتحصيل 1% = 1,220.
- استرداد الدفعة المقدمة 20% يساوي 24,400، لكن المتبقي من الدفعة **14,000** فقط (32,000 − 18,000)، فيسترد البرنامج 14,000 ويتوقف.
- المستحق: 122,000 − 6,100 − 14,000 − 1,220 = **100,680**.
| الحساب | مدين | دائن |
|---|---|---|
| 5020 تكاليف مقاولي الباطن | 122,000.00 |  |
| 2015 مقاولو الباطن (ذمم دائنة) |  | 100,680.00 |
| 2110 محتجزات مقاولي الباطن |  | 6,100.00 |
| 1310 دفعات مقدمة للموردين ومقاولي الباطن |  | 14,000.00 |
| 2210 ضريبة الخصم والتحصيل المستحقة |  | 1,220.00 |
| **الإجمالي** | **122,000.00** | **122,000.00** |

![المستخلص 2 بعد الترحيل](help/sub-cert2-ar.jpg)

## السداد ورد المحتجزات
- **صرف** 167,280 (66,600 + 100,680) يسدد المستحق:
| الحساب | مدين | دائن |
|---|---|---|
| 2015 مقاولو الباطن (ذمم دائنة) | 167,280.00 |  |
| 1020 النقدية بالبنوك |  | 167,280.00 |
| **الإجمالي** | **167,280.00** | **167,280.00** |
- **رد المحتجزات** في نهاية فترة الصيانة ينقل الـ 10,600 المحتجزة إلى المستحق، ثم تُصرف كأي مبلغ آخر:
| الحساب | مدين | دائن |
|---|---|---|
| 2110 محتجزات مقاولي الباطن | 10,600.00 |  |
| 2015 مقاولو الباطن (ذمم دائنة) |  | 10,600.00 |
| **الإجمالي** | **10,600.00** | **10,600.00** |

> ضريبة الخصم والتحصيل المخصومة (2,120 هنا) تبقى في *ضريبة الخصم والتحصيل المستحقة* حتى تُسدد لمصلحة الضرائب (نموذج **سداد ضريبة الخصم والتحصيل**).

> يمكن إعادة فتح المستخلص لتصحيحه فقط ما دام لا يعتمد عليه شيء بعده (مثلاً قبل رد محتجزاته).$g$, 1, true, 'BeyondERP'
from s where exists (select 1 from public.help_categories where key = $g$projects$g$);

with s as (insert into public.help_seeded(key) values ($g$guide-2026-11-09$g$) on conflict (key) do nothing returning key)
insert into public.help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
select $g$partners$g$, $g$Partners: how profit available to distribute is worked out$g$, $g$الشركاء: كيف يُحسب الربح القابل للتوزيع$g$, $g$Profit on paper is not all money the partners can take: a running project still has costs to pay. BeyondERP holds back, from each project's billed profit, the cost that project still needs, and shows what is left as **clean net profit**: what the partners can distribute from.

## The rule, per project
1. **Expected total cost** = expected revenue (or revenue to date, if more) × expected cost %.
2. **Cost still to come** = expected total cost − cost to date (zero once the project is completed or closed).
3. **Held for remaining costs** = the smaller of the billed profit and the cost still to come.
4. **Clean net profit** = billed profit − held.

## A worked example: an office fit-out
Expected revenue 1,000,000 at an expected cost of 80%. Billed so far 900,000; costs so far 600,000.

| | Amount |
|---|---|
| Billed profit (900,000 − 600,000) | 300,000.00 |
| Expected total cost (1,000,000 × 80%) | 800,000.00 |
| Cost still to come (800,000 − 600,000) | 200,000.00 |
| Held for remaining costs | 200,000.00 |
| **Clean net profit** | **100,000.00** |

The project is 75% complete by cost (600,000 of 800,000).

![The Partners' Report Pack: billed profit 300,000, held 200,000, clean 100,000](help/partners-pack-en.jpg)

## Where to set it
- Each project's **expected revenue** and **expected cost %** on **Business Units → Business Units (Cost Centres)**. Projects without their own % use the company default.
- The report is **Financial Reports → Partners' Report Pack**, in English or Arabic, exportable to PDF, Excel or Word.

> Early in a project the held amount often equals the whole billed profit, so clean profit is 0. That is correct: the money will be needed to finish the work.$g$, $g$الربح على الورق ليس كله مالاً يستطيع الشركاء أخذه: المشروع الجاري ما زالت عليه تكاليف. يحجز BeyondERP من الربح المفوتر لكل مشروع التكلفة التي ما زال يحتاجها، ويعرض الباقي كـ **صافي الربح بعد التكاليف المتبقية**: وهو ما يوزّع منه الشركاء.

## القاعدة لكل مشروع
1. **التكلفة الإجمالية المتوقعة** = الإيراد المتوقع (أو الإيراد حتى تاريخه إن كان أكبر) × نسبة التكلفة المتوقعة.
2. **التكلفة المتبقية** = التكلفة الإجمالية المتوقعة − التكلفة حتى تاريخه (صفر عند اكتمال المشروع أو إغلاقه).
3. **المحتجز للتكاليف المتبقية** = الأصغر من الربح المفوتر والتكلفة المتبقية.
4. **صافي الربح بعد التكاليف المتبقية** = الربح المفوتر − المحتجز.

## مثال محلول: تجهيز مكاتب
الإيراد المتوقع 1,000,000 بتكلفة متوقعة 80%. المفوتر حتى الآن 900,000، والتكاليف حتى الآن 600,000.

| | المبلغ |
|---|---|
| الربح المفوتر (900,000 − 600,000) | 300,000.00 |
| التكلفة الإجمالية المتوقعة (1,000,000 × 80%) | 800,000.00 |
| التكلفة المتبقية (800,000 − 600,000) | 200,000.00 |
| المحتجز للتكاليف المتبقية | 200,000.00 |
| **صافي الربح بعد التكاليف المتبقية** | **100,000.00** |

نسبة الإنجاز بالتكلفة 75% (600,000 من 800,000).

![حزمة تقارير الشركاء: الربح المفوتر 300,000، المحتجز 200,000، الصافي 100,000](help/partners-pack-ar.jpg)

## أين تُضبط
- **الإيراد المتوقع** و**نسبة التكلفة المتوقعة** لكل مشروع من **وحدات الأعمال ← وحدات الأعمال (مراكز التكلفة)**. والمشروع بلا نسبة خاصة يستخدم نسبة الشركة الافتراضية.
- التقرير في **التقارير المالية ← حزمة تقارير الشركاء** بالعربية أو الإنجليزية، ويُصدّر بصيغة PDF أو Excel أو Word.

> في بداية المشروع يساوي المحتجز غالباً الربح المفوتر كله، فيكون الصافي صفراً. وهذا صحيح: المال سيلزم لإكمال العمل.$g$, 1, true, 'BeyondERP'
from s where exists (select 1 from public.help_categories where key = $g$partners$g$);

with s as (insert into public.help_seeded(key) values ($g$guide-2026-11-10$g$) on conflict (key) do nothing returning key)
insert into public.help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
select $g$files$g$, $g$Importing from Excel, Google Sheets or an older BEYOND workbook$g$, $g$الاستيراد من Excel أو Google Sheets أو مصنف BEYOND سابق$g$, $g$Everything goes through **Administration → Data Import & Export**.

![Data Import & Export](help/import-data-en.jpg)

## Which file to use
- **The BeyondERP workbook (.xlsx)**, in English or Arabic: download **Import template** (empty, or with demo data), fill the sheets you need and import it. A full exported workbook also imports back, like a backup.
- **Your own sheets**: import them, then match your columns to ours on the preview screen.
- **Google Sheets**: paste the link. The sheet must be shared as *Anyone with the link can view*.
- **Older BEYOND workbooks (V0.6 and later)**: choose **Legacy BEYOND workbook**. The journal, projects, partners, BOQs, invoices and price list are read and converted. Each conversion is listed in the notes after the import, in your language.

## Importing safely
1. Choose a **mode**: *New records only*, *All records (add and update)*, or (super administrator only) *Replace all data*.
2. The preview checks every row first. If any row has a problem, **nothing** is imported, and each problem is listed with its sheet, row and reason.
3. Rows dated after today are imported as dated but listed, because a mistyped year leaves them out of today's balances.
4. After importing, open **Financial Reports → Model Audit**: every integrity check should pass.

## Drop-downs in the Arabic workbook: the code guide
Cells with a drop-down keep short **codes** (for example `Issue`, `Posted`, `general_admin`), because the workbook's formulas and the import read them. The **code guide** sheet (دليل الأكواد) lists every code with its meaning in Arabic, and clicking a drop-down cell shows the same meanings as a hint.

![The code guide sheet in the Arabic workbook](help/code-guide.jpg)

> Exported workbooks have no frozen rows or columns, and their formulas recalculate in Excel, LibreOffice and Google Sheets.$g$, $g$كل شيء يتم من **الإدارة والنظام ← استيراد وتصدير البيانات**.

![استيراد وتصدير البيانات](help/import-data-ar.jpg)

## أي ملف تستخدم
- **مصنف BeyondERP (.xlsx)** بالعربية أو الإنجليزية: نزّل **قالب الاستيراد** (فارغاً أو ببيانات تجريبية)، واملأ الأوراق التي تحتاجها واستورده. والمصنف الكامل المُصدَّر يُستورد أيضاً كنسخة احتياطية.
- **أوراقك الخاصة**: استوردها ثم طابق أعمدتك مع أعمدتنا في شاشة المعاينة.
- **Google Sheets**: الصق الرابط. يجب أن تكون الورقة مشاركة بخيار *أي شخص لديه الرابط يمكنه العرض*.
- **مصنفات BEYOND السابقة (V0.6 وما بعدها)**: اختر **مصنف BEYOND سابق**. يُقرأ دفتر اليومية والمشروعات والشركاء والمقايسات والفواتير ومكتبة الأسعار ويُحوّل، وتُعرض كل عملية تحويل في الملاحظات بعد الاستيراد بلغتك.

## الاستيراد بأمان
1. اختر **الوضع**: *السجلات الجديدة فقط*، أو *جميع السجلات (إضافة وتحديث)*، أو (للمسؤول الأعلى فقط) *استبدال جميع البيانات*.
2. تفحص المعاينة كل صف أولاً. إذا كان في أي صف مشكلة **لا** يُستورد شيء، وتُعرض كل مشكلة بورقتها وصفها وسببها.
3. الصفوف بتاريخ بعد اليوم تُستورد بتواريخها لكنها تُذكر، لأن خطأ كتابة السنة يُخرجها من أرصدة اليوم.
4. بعد الاستيراد افتح **التقارير المالية ← مراجعة الدفاتر**: يجب أن تنجح كل فحوص السلامة.

## القوائم المنسدلة في المصنف العربي: دليل الأكواد
الخلايا ذات القائمة المنسدلة تحتفظ **بأكواد** قصيرة (مثل `Issue` و`Posted` و`general_admin`)، لأن معادلات المصنف والاستيراد تعتمد عليها. تعرض ورقة **دليل الأكواد** كل كود ومعناه بالعربية، والضغط على خلية القائمة يُظهر المعاني نفسها كتلميح.

![ورقة دليل الأكواد في المصنف العربي](help/code-guide.jpg)

> المصنفات المُصدَّرة بلا صفوف أو أعمدة مثبتة، ومعادلاتها تُعاد حسابها في Excel وLibreOffice وGoogle Sheets.$g$, 1, true, 'BeyondERP'
from s where exists (select 1 from public.help_categories where key = $g$files$g$);
