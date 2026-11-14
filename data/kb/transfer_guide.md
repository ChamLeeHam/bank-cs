---
title: 转账与收款指南 / Transfer and receiving guide
intent: transfer_howto
---
<!-- 狮城银行（演示）Lion City Bank (Demo)。本行自己的规则、限额和时间是虚构的；PayNow 昵称的变化参考了新加坡银行公会（ABS）的公开资料。 -->

## 转账方式比较 / Ways to transfer money
<!-- sub_intents: transfer_into_account, receiving_money -->
PayNow：用手机号码、NRIC 或 UEN 转账，实时到账，全天 24 小时可用。FAST：用银行账号转给新加坡任何银行，通常几分钟内到账，每笔最高 SGD 200,000。GIRO：用于定期账单扣款（如水电费、保险），按账单机构的周期扣款。海外电汇（TT）：汇款到新加坡以外的银行账户。

PayNow: send to a mobile number, NRIC or UEN; arrives instantly, 24/7. FAST: send to any Singapore bank account by account number; usually arrives within minutes, up to SGD 200,000 per transfer. GIRO: recurring bill payments such as utilities and insurance, deducted on the billing organisation's schedule. Overseas telegraphic transfer (TT): send money to a bank account outside Singapore.

## 转账一般多久到账 / How long transfers take
<!-- sub_intents: transfer_timing, balance_not_updated_after_bank_transfer, pending_transfer -->
PayNow 和 FAST 通常在几秒到几分钟内到账，全天可用。GIRO 按账单机构的扣款日处理。海外电汇在新加坡工作日下午 3 点前提交，当天处理；一般 1–3 个工作日到账，部分国家或地区可能需要 5 个工作日。汇入的海外电汇在本行收到后当天入账。如果超过这些时间仍未到账，可以登录后让我们查询您那一笔转账的状态。

PayNow and FAST usually arrive within seconds to minutes, 24/7. GIRO is processed on the billing organisation's deduction date. Overseas TTs submitted before 3 pm on a Singapore business day are processed the same day and usually arrive in 1–3 working days; some countries can take up to 5 working days. Incoming overseas TTs are credited on the day we receive them. If a transfer is later than this, log in and ask us to check the status of that specific transfer.

## 如何汇款到海外 / How to send money overseas
<!-- sub_intents: transfer_into_account, transfer_timing -->
在 App 中选择"转账 > 海外汇款 > 新增收款人"，填写收款人全名、账号、收款银行名称和 SWIFT 代码，以及收款人地址。确认金额和费用后，输入短信验证码（OTP）即可提交。首次向新收款人汇款，需要经过 12 小时冷静期（见"收款人限制"）。

In the app, go to Transfer > Overseas transfer > Add payee and enter the payee's full name, account number, bank name, SWIFT code and address. Check the amount and fees, then enter the one-time password (OTP) sent to your phone to submit. The first transfer to a new payee has a 12-hour cooling period (see "Payee restrictions").

## 如何收款 / How to receive money
<!-- sub_intents: receiving_money, transfer_into_account -->
如果朋友或家人从美国等海外地区向您汇款（international transfer / incoming overseas TT），请把以下收款资料提供给付款人：收款银行 SWIFT/BIC 代码 LCBKSGSGXXX、银行名称 Lion City Bank (Demo)、您的账户全名和账号。多币种账户可以直接收取 USD、CNY、MYR 等外币，无需先兑换成新元。新加坡本地收款则可提供您的账号和银行代码 7999，或在 App 中把 PayNow 绑定到手机号码或 NRIC；PayNow 资料不能替代海外电汇的 SWIFT 收款资料。

If a friend or family member is sending you money from the US or another country by international transfer / incoming overseas TT, give them these receiving details: SWIFT/BIC code LCBKSGSGXXX, bank name Lion City Bank (Demo), your full account name and account number. A multi-currency account can receive USD, CNY, MYR and other currencies directly, without converting to SGD first. For local Singapore transfers, give your account number and bank code 7999, or link PayNow to your mobile number or NRIC in the app; PayNow details do not replace the SWIFT details needed for an incoming overseas TT.

## 收款人限制 / Payee restrictions
<!-- sub_intents: beneficiary_not_allowed -->
为防范诈骗，新增收款人后的 12 小时内，向该收款人的转账每天最多 SGD 1,000。以下情况无法添加收款人：收款人姓名与账户不符、收款国家或地区受制裁、收款方为加密货币交易平台（本行不支持向加密货币平台转账）。如果添加收款人失败，请核对姓名和账号是否与对方银行记录完全一致。

To protect you from scams, transfers to a newly added payee are limited to SGD 1,000 per day for the first 12 hours. A payee cannot be added if the name does not match the account, if the country is under sanctions, or if the payee is a cryptocurrency exchange (we do not support transfers to crypto platforms). If adding a payee fails, check that the name and account number match the receiving bank's records exactly.

## 转账限额 / Transfer limits
<!-- sub_intents: transfer_into_account, declined_transfer -->
PayNow 和 FAST 的默认每日限额为 SGD 5,000，可在 App 的"设置 > 转账限额"中调整，最高 SGD 200,000。降低限额立即生效；提高限额需等待 12 小时才生效，这是防诈骗措施。海外电汇的每日限额另行设置，默认 SGD 20,000。

The default daily limit for PayNow and FAST is SGD 5,000. You can change it in the app under Settings > Transfer limits, up to SGD 200,000. Lowering the limit takes effect immediately; raising it takes effect after 12 hours as an anti-scam measure. Overseas TTs have a separate daily limit, SGD 20,000 by default.

## 转账失败的常见原因 / Common reasons a transfer fails
<!-- sub_intents: failed_transfer, declined_transfer, transfer_not_received_by_recipient -->
常见原因包括：收款人姓名与账号不符、收款银行代码或 SWIFT 代码错误、超过每日转账限额、账户余额不足，以及转账正在进行例行合规审核。被退回的款项一般在 3–5 个工作日内退回您的账户，手续费可能不退。想了解您某一笔转账为什么失败，可以登录后让我们查询该笔转账。

Common reasons include: the payee name does not match the account number, the bank code or SWIFT code is wrong, the daily transfer limit was exceeded, the balance was not enough, or the transfer is under routine compliance review. Returned funds usually come back to your account within 3–5 working days; the transfer fee may not be refunded. To find out why one of your own transfers failed, log in and ask us to check that transfer.

## PayNow 收款人名字的显示 / How PayNow shows the payee's name
<!-- sub_intents: transfer_into_account -->
用 PayNow 转账时，确认之前 App 会显示收款人登记的名字，其中一部分字母会被隐藏。请核对名字和您要转的人是不是一致，对不上就不要转。为了防止冒名诈骗，新加坡银行公会（ABS）从 2026 年 6 月 6 日起取消了个人客户的 PayNow 昵称功能，以前设置的昵称不会再显示，现在只显示部分隐藏的登记名字。

When you pay by PayNow, the app shows the payee's registered name, partly masked, before you confirm. Check that the name matches the person you mean to pay, and do not continue if it does not. To prevent impersonation scams, the Association of Banks in Singapore (ABS) removed the PayNow nickname feature for individual customers from 6 June 2026. Nicknames set before are no longer shown; only the partly masked registered name appears.

## PayNow 转错收款人怎么办 / What to do after sending PayNow to the wrong person
<!-- sub_intents: transfer_into_account, transfer_timing -->
PayNow 转账前先核对 App 显示的收款人姓名和金额；如果已经转给错误的人，请尽快联系对方说明情况并请求退回。如果对方没有回应，请联系自己的银行寻求协助。银行通常不能在未取得收款人授权的情况下直接从对方账户扣回款项，所以不要假设转账一定能即时撤销。若你怀疑这是诈骗或账户被盗用，请立即联系银行并报告可疑交易。

Before confirming a PayNow transfer, check the displayed payee name and amount. If you have already sent money to the wrong person, contact the recipient promptly, explain the mistake and ask for the funds to be returned. If they do not respond, contact your bank for help. A bank generally cannot debit the recipient's account without the recipient's authorisation, so do not assume the transfer can be reversed immediately. If you suspect a scam or account compromise, contact your bank and report the suspicious transaction immediately.

## 收到不明的 PayNow 转账 / An unexpected PayNow payment
<!-- sub_intents: receiving_money -->
如果账户收到不认识的 PayNow 款项，不要花掉这笔钱，也不要按照陌生人的要求转到另一个账户。请通过银行 App 或银行卡背面的官方联系方式联系银行，说明入账时间、金额和交易参考号，并让银行指引后续处理。不要仅凭来电者或短信提供的号码联系对方，也不要透露密码或 OTP。

If an unfamiliar PayNow payment appears in your account, do not spend it or send it to another account at a stranger's request. Contact your bank through its official app or the number on your card, and provide the time, amount and transaction reference so the bank can advise you. Do not rely on a phone number supplied by a caller or text message, and never disclose your password or OTP.

## FAST 与 GIRO 有什么区别 / FAST versus GIRO
<!-- sub_intents: transfer_into_account, transfer_timing -->
在新加坡，FAST 用于参与机构之间的新元转账，通常几乎即时到账，全天候可用；eGIRO 是授权账单机构从账户扣款的方式，常用于水电费、税费等定期付款，处理时间可能较长。具体到账时间、可用账户和服务限制由各参与机构决定；如果需要查某一笔款项，请以银行交易记录和该机构的说明为准。本节是新加坡通用支付方式说明，不代表狮城银行演示系统的具体时限。

In Singapore, FAST moves SGD between participating institutions, usually almost immediately and around the clock. eGIRO is an authorised direct-debit method used by billing organisations for recurring payments such as utilities and taxes, and processing can take longer. Exact timing, eligible accounts and service limits depend on the participating institutions. For a specific payment, check your bank transaction record and the billing organisation's guidance. This is general information about Singapore payment schemes, not a service-time promise for the Lion City Bank demo.

## PayNow 可以用哪些资料收款 / PayNow identifiers
<!-- sub_intents: receiving_money, transfer_into_account -->
PayNow 可使用已登记的手机号码、NRIC/FIN、UEN 或参与机构支持的虚拟付款地址（VPA）识别收款人。可用标识取决于收款人登记的资料和所用服务；转账前要在银行 App 核对显示的收款人名称，不要只凭聊天中发来的号码确认。PayNow 是新加坡本地新元支付方式，不等于海外电汇。

PayNow can identify a recipient using a registered mobile number, NRIC/FIN, UEN or a virtual payment address (VPA) supported by the participating service. Available identifiers depend on the recipient's registration and provider. Check the payee name shown in your banking app before confirming; do not rely only on a number sent in a chat. PayNow is a domestic Singapore-dollar payment method, not an overseas wire transfer.

## 使用 PayNow QR 或 SGQR 付款 / Paying with PayNow QR or SGQR
<!-- sub_intents: transfer_into_account -->
在银行 App 中打开扫码付款功能，扫描带有 PayNow 标识的 QR/SGQR 后，先核对商户名称和付款金额，再确认交易。二维码可能预填收款人或金额，但仍应检查最终确认页面；如果商户名称、金额或用途不符，不要输入 OTP 或继续付款。不同银行 App 的入口和可用功能可能不同。

Open the QR payment feature in your banking app and scan a QR code or SGQR showing the PayNow logo. Check the merchant name and amount before confirming. A QR code may pre-fill the recipient or amount, but review the final confirmation screen; if the merchant, amount or purpose looks wrong, do not enter an OTP or proceed. The menu and available features vary by banking app.

## FAST 转账适合什么场景 / When to use FAST
<!-- sub_intents: transfer_into_account, transfer_timing -->
FAST 是参与金融机构之间的新元电子转账服务，适合需要较快转账的本地付款；参与机构通常全天候提供服务。FAST 的具体限额、可用账户、处理状态和任何费用由各机构决定。若 App 显示待处理或失败，请不要为了“加快到账”重复提交；先查看交易记录和参考编号，再向自己的银行查询。

FAST is a Singapore-dollar electronic transfer service between participating financial institutions and is commonly used for local payments that need quick processing. Participating institutions generally make it available around the clock. Each provider sets its own limits, eligible accounts, status handling and any fees. If the app shows pending or failed, do not submit the transfer again just to make it faster; check the transaction record and reference first, then ask your bank.

## GIRO 扣款需要什么授权 / How GIRO debit authorisation works
<!-- sub_intents: transfer_into_account, receiving_money -->
GIRO 直接扣款通常需要客户向账单机构提供授权，让该机构通过银行收取约定款项。设置前先核对账单机构、付款账户和授权内容；不再需要时，应按账单机构和银行提供的流程取消或修改授权。已经提交的扣款能否撤回、何时生效，要向相关机构确认。

A GIRO direct debit generally requires the customer to authorise a billing organisation to collect agreed payments through the bank. Before setting it up, check the biller, funding account and authorisation details. If you no longer need it, follow the cancellation or amendment process provided by the biller and bank. Ask them whether a submitted debit can be stopped and when a change takes effect.

## eGIRO 授权没有马上生效怎么办 / When an eGIRO mandate is not active yet
<!-- sub_intents: transfer_timing, receiving_money -->
eGIRO 通过线上流程建立账单扣款授权，但提交申请不一定代表授权已生效。请查看银行 App 或账单机构页面显示的状态，并确认账户资料和验证步骤已完成；在状态确认前，建议使用账单机构提供的其他付款方式，避免逾期。各银行和账单机构的审核时间不同，具体时间以页面提示为准。

eGIRO creates a direct-debit authorisation online, but submitting a request does not necessarily mean the mandate is active. Check the status in your banking app or the biller's site and make sure the account details and verification steps are complete. Until activation is confirmed, consider another payment method offered by the biller to avoid a late payment. Processing times vary by bank and biller; follow the status shown to you.

## FAST、GIRO 和 MEPS+ 怎么选 / Choosing between FAST, GIRO and MEPS+
<!-- sub_intents: transfer_into_account, transfer_timing -->
一般来说，FAST 用于参与机构之间较快的本地新元转账；GIRO 用于经授权的定期扣款或账单付款；MEPS+ 用于通过银行处理较高金额或特定的大额新元转账。三者的资格、截止时间、费用和到账时间并不相同。付款前请查看银行 App 对该笔交易的实际选项和费用说明，不要只按金额自行判断能否使用。

In general, FAST is used for quicker local SGD transfers between participating institutions; GIRO is used for authorised recurring debits or bill payments; and MEPS+ is a bank-processed option for higher-value or other specific SGD transfers. Eligibility, cut-off times, fees and delivery times differ. Check the options and charges shown by your bank for the transaction rather than assuming a method is available based only on the amount.
