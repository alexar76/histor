FROM histor-histor:pre-shortlinks-20261124
USER 0:0
COPY --chown=0:0 --chmod=644 overlay/diffing.py overlay/app.py overlay/security_events.py /app/histor/
COPY --chown=0:0 --chmod=644 overlay/desk.js overlay/i18n.js /app/docs/landing/assets/
COPY --chown=0:0 --chmod=644 overlay/i18n/es.js overlay/i18n/fr.js overlay/i18n/ru.js overlay/i18n/zh.js /app/docs/landing/assets/i18n/
LABEL aicom.histor.overlay="shortlinks-20261124"
USER 65532:65532
