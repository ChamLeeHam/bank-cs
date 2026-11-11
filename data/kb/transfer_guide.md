---
title: 转账与收款指南 / Transfer and receiving guide
intent: transfer_howto
---
<!-- 狮城银行（演示）Lion City Bank (Demo)。本文件中的所有规则、限额和时间均为虚构，仅用于演示。 -->

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
新加坡本地收款：把您的账号和银行代码 7999 告诉付款人，或在 App 中把 PayNow 绑定到您的手机号码或 NRIC。海外汇入：请付款人使用 SWIFT 代码 LCBKSGSGXXX，收款银行名称 Lion City Bank (Demo)，并填写您的账户全名和账号。多币种账户可以直接收取 USD、CNY、MYR 等外币，无需先兑换成新元。

Local transfers: give the sender your account number and bank code 7999, or link PayNow to your mobile number or NRIC in the app. Incoming overseas transfers: ask the sender to use SWIFT code LCBKSGSGXXX, bank name Lion City Bank (Demo), and your full account name and number. A multi-currency account can receive USD, CNY, MYR and other currencies directly, without converting to SGD first.

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
