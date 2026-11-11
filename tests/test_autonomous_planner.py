"""Тесты для автономного планировщика с LLM."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.autonomous_planner import ActionType, AgentAction, AutonomousPlanner, LLMClient


class TestLLMClient:
    """Тесты для LLMClient."""

    def test_init(self):
        """Тест инициализации клиента."""
        client = LLMClient(api_key="test-key", model="gpt-4o")
        assert client.api_key == "test-key"
        assert client.base_url == "https://api.openai.com/v1"
        assert client.model == "gpt-4o"

    async def test_complete_success(self):
        """Тест успешного ответа от LLM."""
        client = LLMClient(api_key="test-key")
        client._client = None  # Force creation

        # Мокаем HTTP клиент
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"message": {"content": "Тестовый ответ"}}]}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        client._client = mock_client

        result = await client.complete([{"role": "user", "content": "Привет"}])
        assert result == "Тестовый ответ"

    async def test_complete_error(self):
        """Тест ошибки при запросе к LLM."""
        client = LLMClient(api_key="test-key")
        client._client = None

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = Exception("Server error")

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        client._client = mock_client

        with pytest.raises(Exception):
            await client.complete([{"role": "user", "content": "Тест"}])


class TestAgentAction:
    """Тесты для AgentAction."""

    def test_create_action(self):
        """Тест создания действия."""
        action = AgentAction(
            type=ActionType.OPEN,
            target="https://ozon.ru",
            reason="Открываем сайт Озон",
            requires_confirmation=False,
        )
        assert action.type == ActionType.OPEN
        assert action.target == "https://ozon.ru"
        assert action.value is None
        assert action.reason == "Открываем сайт Озон"
        assert action.requires_confirmation is False

    def test_to_dict(self):
        """Тест сериализации действия в словарь."""
        action = AgentAction(
            type=ActionType.CLICK,
            target="button.submit",
            value=None,
            reason="Клик по кнопке отправки",
            requires_confirmation=True,
        )
        d = action.to_dict()
        assert d["type"] == "кликнуть"
        assert d["target"] == "button.submit"
        assert d["value"] is None
        assert d["reason"] == "Клик по кнопке отправки"
        assert d["requires_confirmation"] is True


class TestAutonomousPlanner:
    """Тесты для AutonomousPlanner."""

    def test_init(self):
        """Тест инициализации планировщика."""
        client = LLMClient(api_key="test-key")
        planner = AutonomousPlanner(client)
        assert planner.client == client
        assert planner.SYSTEM_PROMPT is not None
        assert len(planner.SYSTEM_PROMPT) > 0

    async def test_decide_next_action(self):
        """Тест принятия решения о следующем действии."""
        client = LLMClient(api_key="test-key")
        planner = AutonomousPlanner(client)

        # Мокаем метод complete
        async def mock_complete(messages):
            return (
                '{"type": "открыть", "target": "https://ozon.ru", "value": null, '
                '"reason": "Открываем сайт Озон", "requires_confirmation": false}'
            )

        client.complete = mock_complete

        action = await planner.decide_next_action(
            task_description="Найти товар на Ozon",
            conversation_history=[],
            available_tools=["browser", "shell", "http", "memory"],
        )

        assert action.type == ActionType.OPEN
        assert action.target == "https://ozon.ru"
        assert action.requires_confirmation is False

    async def test_decide_next_action_with_confirmation(self):
        """Тест решения с подтверждением пользователя."""
        client = LLMClient(api_key="test-key")
        planner = AutonomousPlanner(client)

        async def mock_complete(messages):
            return (
                '{"type": "завершить", "target": null, "value": "Задача выполнена", '
                '"reason": "Задача завершена успешно", "requires_confirmation": false}'
            )

        client.complete = mock_complete

        action = await planner.decide_next_action(
            task_description="Тестовая задача",
            conversation_history=[],
            available_tools=["browser"],
        )

        assert action.type == ActionType.FINISH
        assert action.value == "Задача выполнена"
        assert action.requires_confirmation is False

    async def test_parse_nested_action_format(self):
        """Формат из системного промпта: {"thought": ..., "action": {...}}."""
        client = LLMClient(api_key="test-key")
        planner = AutonomousPlanner(client)

        async def mock_complete(messages):
            return (
                '{"thought": "Нужно открыть сайт", '
                '"action": {"type": "открыть", "target": "https://ozon.ru", '
                '"value": null, "reason": "старт", "requires_confirmation": false}}'
            )

        client.complete = mock_complete

        action = await planner.decide_next_action("Найти товар", [], ["browser"])
        assert action.type == ActionType.OPEN
        assert action.target == "https://ozon.ru"
        assert action.reason == "старт"

    async def test_parse_nested_action_uses_thought_as_reason(self):
        """Если reason не задан, в качестве обоснования берётся thought."""
        client = LLMClient(api_key="test-key")
        planner = AutonomousPlanner(client)

        async def mock_complete(messages):
            return '{"thought": "Проверю заголовок", "action": {"type": "заголовок"}}'

        client.complete = mock_complete

        action = await planner.decide_next_action("Задача", [], ["browser"])
        assert action.type == ActionType.TITLE
        assert action.reason == "Проверю заголовок"

    async def test_parse_invalid_response(self):
        """Тест обработки некорректного ответа LLM."""
        client = LLMClient(api_key="test-key")
        planner = AutonomousPlanner(client)

        async def mock_complete(messages):
            return "Невалидный ответ без JSON"

        client.complete = mock_complete

        action = await planner.decide_next_action(
            task_description="Тест",
            conversation_history=[],
            available_tools=["browser"],
        )

        # При ошибке парсинга должен вернуться безопасный ответ
        assert action.type == ActionType.FINISH
        assert "Ошибка парсинга" in action.value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
