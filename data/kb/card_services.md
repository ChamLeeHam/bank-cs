---
title: 银行卡服务 / Card services
intent: card_service
---
<!-- 狮城银行（演示）Lion City Bank (Demo)。本文件中的所有规则、费用和时间均为虚构，仅用于演示。 -->

## 激活新卡 / Activating a new card
<!-- sub_intents: activate_my_card -->
收到新卡后，在 App 中选择"银行卡 > 激活"，输入卡片背面的后四位并完成短信验证码（OTP）验证即可激活。也可以在狮城银行 ATM 插卡并设置 PIN 来激活。卡片激活前不能消费或取款。

When your new card arrives, go to Cards > Activate in the app, enter the last four digits on the back of the card and confirm with the one-time password (OTP). You can also activate it by inserting the card at a Lion City Bank ATM and setting a PIN. The card cannot be used for payments or withdrawals until it is activated.

## 新卡寄送时间 / Card delivery time
<!-- sub_intents: card_arrival, card_delivery_estimate -->
新卡或补发的卡会在 5–7 个工作日内以挂号信寄到您登记的新加坡地址，App 中可以查看寄送进度。超过 10 个工作日仍未收到，请联系我们补寄。目前不支持把卡寄到新加坡以外的地址。

New and replacement cards are sent by registered mail to your Singapore address within 5–7 working days; you can track delivery in the app. If you have not received it after 10 working days, contact us to resend it. We cannot currently send cards to addresses outside Singapore.

## 修改 PIN 与 PIN 被锁 / Changing or unblocking your PIN
<!-- sub_intents: change_pin, pin_blocked -->
修改 PIN：在 App 的"银行卡 > 修改 PIN"中操作，或在狮城银行 ATM 上修改。连续输错 PIN 3 次后，PIN 会被锁定，卡片暂时不能刷卡或取款。解锁方法：在 App 中选择"银行卡 > 解锁 PIN"并完成 OTP 验证，解锁后需重新设置 PIN。

To change your PIN, use Cards > Change PIN in the app or any Lion City Bank ATM. After 3 wrong PIN attempts in a row, the PIN is blocked and the card cannot be used for payments or withdrawals. To unblock it, go to Cards > Unblock PIN in the app, confirm with the OTP, and set a new PIN.

## 卡被拒或不能用 / Card declined or not working
<!-- sub_intents: card_not_working, declined_card_payment, declined_cash_withdrawal, contactless_not_working -->
卡被拒的常见原因：余额不足、超过每日消费或取款限额、没有开通海外使用、卡片已冻结、卡片未激活或已过期。感应支付（contactless）单笔超过 SGD 200 时，需要插卡并输入 PIN。卡片芯片损坏时请申请补卡。可以在 App 的"银行卡 > 卡片设置"中查看状态、开通海外使用和调整限额。

Common reasons a card is declined: not enough balance, the daily spending or withdrawal limit is reached, overseas use is not turned on, the card is frozen, or the card is not activated or has expired. Contactless payments over SGD 200 need the card inserted with a PIN. If the chip is damaged, request a replacement card. You can check the card status, turn on overseas use and change limits under Cards > Card settings in the app.

## ATM 吞卡 / Card retained by an ATM
<!-- sub_intents: card_swallowed -->
ATM 吞卡通常是为了安全（例如多次输错 PIN 或卡片读取失败）。被吞的卡会被安全销毁，不会退还。请在 App 中申请补卡，ATM 吞卡的补卡免收费用，新卡 5–7 个工作日寄到。如果担心卡片被盗用，可以先在 App 中冻结卡片。

ATMs retain cards for security, for example after several wrong PIN attempts or a card read error. Retained cards are securely destroyed and cannot be returned. Request a replacement in the app; it is free when an ATM kept your card, and the new card arrives in 5–7 working days. If you are worried about misuse, freeze the card in the app first.

## 卡片即将到期 / Card about to expire
<!-- sub_intents: card_about_to_expire -->
卡片到期前一个月，我们会自动把新卡寄到您登记的地址，卡号不变，有效期和安全码（CVV）会更新。收到后请激活新卡，并在使用自动扣款的商户（如订阅服务）中更新有效期和 CVV。

One month before your card expires, we automatically post a new card to your registered address. The card number stays the same; the expiry date and security code (CVV) change. Activate the new card when it arrives and update the expiry date and CVV with merchants that charge you automatically, such as subscriptions.

## 补发实体卡与附属卡 / Replacement and supplementary cards
<!-- sub_intents: get_physical_card, order_physical_card, getting_spare_card -->
在 App 中选择"银行卡 > 申请补卡"即可补发实体卡，费用 SGD 15（卡片被盗并已报警则免费）。信用卡可为 18 岁以上的家人申请附属卡，首两张免年费。借记卡不提供附属卡，每个账户只能关联一张借记卡。

Order a replacement physical card under Cards > Replace card in the app; it costs SGD 15, or is free if the card was stolen and you have a police report. Credit card holders can add supplementary cards for family members aged 18 or above, with no annual fee for the first two. Debit cards have no supplementary cards; each account has one debit card.

## 卡组织与受理范围 / Card networks and where cards are accepted
<!-- sub_intents: visa_or_mastercard, card_acceptance, atm_support -->
借记卡为 Visa，可在全球 Visa 商户和 ATM 使用；信用卡为 Mastercard；另提供银联（UnionPay）借记卡，适合在中国大陆消费，外币交易费只有 1%。所有卡片都支持 Apple Pay 和 Google Pay。在新加坡可使用狮城银行 ATM 以及贴有 Visa、Mastercard 或银联标志的其他银行 ATM。

Our debit card is Visa and works at Visa merchants and ATMs worldwide; our credit card is Mastercard. We also offer a UnionPay debit card for spending in mainland China, with a 1% foreign transaction fee. All cards work with Apple Pay and Google Pay. In Singapore you can use Lion City Bank ATMs and other banks' ATMs showing the Visa, Mastercard or UnionPay logo.
