"""LLM-планировщик для автономного агента.

Отвечает за выбор следующего действия на основе текущего состояния задачи
и результатов предыдущих шагов. Использует OpenAI-совместимый API.
"""

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ActionType(StrEnum):
    OPEN = "открыть"
    FILL = "заполнить"
    CLICK = "кликнуть"
    SCROLL = "прокрутить"
    WAIT = "подождать"
    READ = "прочитать"
    SCREENSHOT = "скриншот"
    TITLE = "заголовок"
    REMEMBER = "запомнить"
    CONFIRM_USER = "подтвердить_с_пользователем"
    FINISH = "завершить"


@dataclass
class AgentAction:
    """Единое действие агента в браузере."""

    type: ActionType
    target: str | None = None  # URL, селектор или текст
    value: str | None = None  # значение для заполнения
    reason: str | None = None  # почему выбрано это действие
    requires_confirmation: bool = False  # нужно ли подтверждение пользователя

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "target": self.target,
            "value": self.value,
            "reason": self.reason,
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass
class StepResult:
    """Результат выполнения одного шага агента."""

    action: AgentAction
    success: bool
    observation: str  # что увидел агент после действия
    error: str | None = None
    screenshot_path: str | None = None
    page_title: str | None = None
    page_url: str | None = None


class LLMClient:
    """Клиент для общения с LLM API (OpenAI-совместимый API).

    Поддерживает OpenAI GPT-4o, GPT-3.5-turbo и локальные модели
    через любой OpenAI-совместимый endpoints (vllm, ollama, llama.cpp).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout_seconds: int = 60,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds,
            )
        return self._client

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Отправить запрос к LLM и получить ответ."""
        client = await self._ensure_client()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class AutonomousPlanner:
    """Автономный планировщик actions на основе LLM.

    Реализует цикл ReAct:
    Thought → Action → Observation → Thought
    """

    SYSTEM_PROMPT = """Ты — автономный браузерный агент. Твоя цель — выполнить поручение пользователя,
используя доступные инструменты браузера.

Доступные действия:
1. открыть: URL — открыть веб-страницу
2. заполнить: селектор: текст — ввести текст в поле ввода
3. кликнуть: селектор — нажать кнопку или ссылку
4. прокрутить: селектор — прокрутить страницу
5. подождать: селектор — подождать появления элемента
6. прочитать: селектор — получить текст с элемента
7. заголовок: — получить заголовок текущей страницы
8. скриншот: — сделать скриншот текущей страницы
9. запомнить: ключ=значение — сохранить факт в памяти задачи
10. завершить: результат — завершить задачу с результатом
11. подтвердить_с_пользователем: вопрос — спросить пользователя перед необратимым действием

Правила:
1. Всегда начинай с открытия подходящего сайта.
2. После каждого действия анализируй результат: что изменилось на странице?
3. Если нужна дополнительная информация от пользователя — спроси её через действие "подтвердить_с_пользователем".
4. Не выполняй оплату или оформление заказа без явного подтверждения пользователя.
5. Если действие не удалось (капча, ошибка сайта) — предложи альтернативу или спроси пользователя.
6. Отвечай в формате JSON:
{"thought": "рассуждение", "action": {"type": "...", "target": "...",
 "value": "...", "reason": "...", "requires_confirmation": true/false}}

Пример:
Пользователь: "Найди билеты в Сочи на завтра"
Ты: {"thought": "Нужно открыть сайт авиабилетов и найти рейсы в Сочи на завтра",
     "action": {"type": "открыть", "target": "https://avia.ru", "value": null,
                "reason": "Открываем сайт авиабилетов для поиска рейсов", "requires_confirmation": false}}
"""

    def __init__(self, client: LLMClient):
        self.client = client

    async def decide_next_action(
        self,
        task_description: str,
        conversation_history: list[dict[str, str]],
        available_tools: list[str],
    ) -> AgentAction:
        """Решить, какое действие сделать дальше на основе текущего состояния."""
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "system", "content": f"Цель пользователя: {task_description}"},
            {"role": "system", "content": f"Доступные инструменты: {', '.join(available_tools)}"},
        ]
        messages.extend(conversation_history[-10:])  # последние 10 сообщений для контекста

        response_text = await self.client.complete(messages)
        return self._parse_response(response_text)

    def _parse_response(self, response_text: str) -> AgentAction:
        """Распарсить ответ LLM в AgentAction."""
        # Ищем JSON в ответе
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start == -1 or end == 0:
            logger.warning("В ответе LLM нет JSON. Текст: %s", response_text[:200])
            return AgentAction(
                type=ActionType.FINISH,
                target=None,
                value="Ошибка парсинга решения модели: в ответе нет JSON",
                reason="Ошибка парсинга ответа модели",
                requires_confirmation=False,
            )

        try:
            data = json.loads(response_text[start:end])

            # Модель отвечает в формате {"thought": ..., "action": {...}}.
            # Поддерживаем и плоский формат {"type": ...} для совместимости.
            payload = data.get("action") if isinstance(data.get("action"), dict) else data
            thought = data.get("thought")

            action_type = ActionType(payload.get("type", "завершить"))
            return AgentAction(
                type=action_type,
                target=payload.get("target"),
                value=payload.get("value"),
                reason=payload.get("reason") or thought,
                requires_confirmation=payload.get("requires_confirmation", False),
            )
        except Exception as e:
            logger.warning("Не удалось распарсить ответ LLM: %s. Текст: %s", e, response_text[:200])
            # Fallback: завершить задачу с ошибкой
            return AgentAction(
                type=ActionType.FINISH,
                target=None,
                value=f"Ошибка парсинга решения модели: {e}",
                reason="Ошибка парсинга ответа модели",
                requires_confirmation=False,
            )
