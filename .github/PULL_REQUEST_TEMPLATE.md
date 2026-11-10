name: Pull Request Template
description: Стандарт оформления pull request
title: "[PR] "
labels: []
body:
  - type: markdown
    attributes:
      value: |
        Спасибо за вклад! Перед отправкой убедитесь, что прочитали [CONTRIBUTING.md](./CONTRIBUTING.md).

  - type: checkboxes
    attributes:
      label: Предварительная проверка
      options:
        - label: Тесты проходят (`pytest`)
          required: true
        - label: Линтер чист (`ruff check .`)
          required: true
        - label: Формат корректен (`ruff format --check .`)
          required: true
        - label: Нет секретов в коммитах
          required: true
        - label: Документация обновлена (если изменился функционал)
          required: false

  - type: textarea
    id: changes
    attributes:
      label: Что изменилось
      description: Опишите изменения по существу
      placeholder: "Добавлен health check для PostgreSQL в /health"
    validations:
      required: true

  - type: textarea
    id: testing
    attributes:
      label: Как протестировано
      description: Какие проверки проведены
      placeholder: "Запущен pytest, проверен эндпоинт /health curl'ем"
    validations:
      required: true

  - type: textarea
    id: breaking
    attributes:
      label: Breaking changes
      description: Есть ли несовместимые изменения?
      placeholder: "Нет / Да, опишите ниже"
    validations:
      required: true
