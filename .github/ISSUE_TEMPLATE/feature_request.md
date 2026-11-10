name: Feature Request
description: Предложить новую функцию или улучшение
title: "[FEATURE] "
labels: ["enhancement", "triage"]
body:
  - type: markdown
    attributes:
      value: |
        У нас открытая дорожная карта. Предложения принимаются с благодарностью.

  - type: input
    id: feature
    attributes:
      label: Название функции
      placeholder: "Интеграция с API Wildberries вместо браузера"
    validations:
      required: true

  - type: textarea
    id: problem
    attributes:
      label: Какую проблему решает эта функция?
      description: Опишите боль клиента или бизнес-задачу
    validations:
      required: true

  - type: textarea
    id: solution
    attributes:
      label: Предлагаемое решение
      description: Как должна работать функция
    validations:
      required: true

  - type: dropdown
    id: priority
    attributes:
      label: Приоритет для тендера
      options:
        - "Критично для MVP"
        - "Желательно в первом релизе"
        - "Бэклог (второй релиз)"
    validations:
      required: true

  - type: checkboxes
    id: sponsorship
    attributes:
      label: Готов ли заказчик спонсировать разработку?
      options:
        - label: Да, функция входит в объём тендера
          required: false
