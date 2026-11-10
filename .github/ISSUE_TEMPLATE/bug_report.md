name: Bug Report
description: Сообщить об ошибке в работе MVP
title: "[BUG] "
labels: ["bug", "triage"]
body:
  - type: markdown
    attributes:
      value: |
        Спасибо за помощь в улучшении MVP! Пожалуйста, заполните все поля.

  - type: input
    id: environment
    attributes:
      label: Окружение
      description: Где воспроизводится ошибка (локально, Docker, VPS клиента)
      placeholder: "Docker на Ubuntu 22.04 / VPS клиента"
    validations:
      required: true

  - type: input
    id: version
    attributes:
      label: Версия
      description: Тег или commit SHA (например, v0.1.0 или a1b2c3d)
      placeholder: "v0.1.0"
    validations:
      required: true

  - type: textarea
    id: description
    attributes:
      label: Описание ошибки
      description: Чётко и по существу, что пошло не так
      placeholder: "При запуске воркера очередь не обрабатывает задачи..."
    validations:
      required: true

  - type: textarea
    id: steps
    attributes:
      label: Шаги для воспроизведения
      description: 1, 2, 3...
      placeholder: |
        1. Запустить `python -m src.queue worker`
        2. Отправить задачу через Telegram-бота
        3. Наблюдать ошибку в логах
    validations:
      required: true

  - type: textarea
    id: expected
    attributes:
      label: Ожидаемое поведение
      placeholder: "Задача должна перейти в статус 'готово'"
    validations:
      required: true

  - type: textarea
    id: actual
    attributes:
      label: Фактическое поведение
      placeholder: "Задача застревает в очереди, в логах AuthenticationError"
    validations:
      required: true

  - type: textarea
    id: logs
    attributes:
      label: Логи и скриншоты
      description: Прикрепите релевантные фрагменты логов (без секретов!)
      placeholder: "Вставьте логи здесь. Не includйте токены, пароли, куки."
    validations:
      required: false

  - type: checkboxes
    id: no-secrets
    attributes:
      label: Проверка секретов
      description: Я確認, что в отчёте нет персональных данных, токенов, паролей или куки
      options:
        - label: Я подтверждаю, что в отчёте нет секретов
          required: true
