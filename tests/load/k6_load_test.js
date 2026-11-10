import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend, Counter } from "k6/metrics";

// Метрики
const errorRate = new Rate("errors");
const responseTime = new Trend("response_time");
const tasksCreated = new Counter("tasks_created");
const tasksCompleted = new Counter("tasks_completed");

// Конфигурация нагрузки
export const options = {
  stages: [
    { duration: "1m", target: 10 },   // Рост до 10 VU
    { duration: "2m", target: 50 },   // Рост до 50 VU
    { duration: "3m", target: 50 },   // Удержание 50 VU
    { duration: "1m", target: 0 },    // Спад до 0
  ],
  thresholds: {
    errors: ["rate<0.1"],             // Ошибок менее 10%
    http_req_duration: ["p(95)<2000"], // 95% запросов менее 2 сек
    http_req_failed: ["rate<0.05"],    // Менее 5% неудачных запросов
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:9090";

export default function () {
  // Тест health endpoint
  const healthRes = http.get(`${BASE_URL}/health`);
  check(healthRes, {
    "health status is 200": (r) => r.status === 200,
    "health response time < 1s": (r) => r.timings.duration < 1000,
  }) || errorRate.add(1);
  responseTime.add(healthRes.timings.duration);

  // Тест создания задачи через API (если есть endpoint)
  const taskRes = http.post(`${BASE_URL}/api/tasks`, JSON.stringify({
    input: "Тестовая задача из k6",
    priority: "med",
  }), {
    headers: { "Content-Type": "application/json" },
  });
  check(taskRes, {
    "task created status is 200 or 201": (r) => r.status === 200 || r.status === 201,
  }) || errorRate.add(1);

  if (taskRes.status === 200 || taskRes.status === 201) {
    tasksCreated.add(1);
    const body = JSON.parse(taskRes.body);
    const taskId = body.task_id || body.id;

    // Проверяем результат задачи
    const resultRes = http.get(`${BASE_URL}/api/tasks/${taskId}/result`);
    check(resultRes, {
      "task result fetched": (r) => r.status === 200 || r.status === 202,
    }) || errorRate.add(1);
    if (resultRes.status === 200) {
      tasksCompleted.add(1);
    }
  }

  sleep(1);
}

export function teardown(data) {
  console.log(`Всего создано задач: ${tasksCreated.count}`);
  console.log(`Всего завершено задач: ${tasksCompleted.count}`);
}