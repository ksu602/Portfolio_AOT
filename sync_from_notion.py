#!/usr/bin/env python3
"""
sync_from_notion.py — Синхронизация данных из Notion → Dashboard HTML

Что исправлено:
- HTML_FILE указывает на trekshon_dashboard.html
- месяцы в таблице метрик считаются ТОЛЬКО от Янв 2026 до Дек 2026
- месяцы в таблице клиентов тоже нормализуются под Янв–Дек 2026
- более аккуратный парсинг названий месяцев и чисел
- безопасная замена блока между NOTION_SYNC_START / NOTION_SYNC_END

Использование:
  NOTION_TOKEN=secret_xxx python3 sync_from_notion.py
"""

import os
import re
import sys
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

# ──────────────────────────────────────────────
# НАСТРОЙКИ
# ──────────────────────────────────────────────
NOTION_TOKEN = os.environ.get('NOTION_TOKEN', '')

# ID страницы «📊 Трекшен — по компаниям» в Notion
TREKSHON_PAGE_ID = '33aaddcc-20f3-81b5-a9c4-c0d940e7ffd9'

# Путь к HTML-файлу дашборда (относительно скрипта)
HTML_FILE = os.path.join(os.path.dirname(__file__), 'trekshon_dashboard.html')

# Компании в том же порядке, что и в дашборде
COMPANIES = [
    'СП RMR (n-ii lab)',
    'НеоАвтоматика',
    'HiveTrace',
    'СейсмикЛаб',
    'ARGUS',
    'Маслов',
]

# ВАЖНО:
# В таблице метрик в Notion месяцы начинаются с Янв 2026.
# В дашборде индекс 3 = Янв 2026, ..., 14 = Дек 2026.
MONTH_SHORT_TO_IDX = {
    'ЯНВ': 3,
    'ФЕВ': 4,
    'МАР': 5,
    'АПР': 6,
    'МАЙ': 7,
    'ИЮН': 8,
    'ИЮЛ': 9,
    'АВГ': 10,
    'СЕН': 11,
    'ОКТ': 12,
    'НОЯ': 13,
    'ДЕК': 14,
}

# Полные названия для таблицы клиентов — тоже только 2026
MONTH_FULL_TO_IDX = {
    'ЯНВ 2026': 3,
    'ФЕВ 2026': 4,
    'МАР 2026': 5,
    'АПР 2026': 6,
    'МАЙ 2026': 7,
    'ИЮН 2026': 8,
    'ИЮЛ 2026': 9,
    'АВГ 2026': 10,
    'СЕН 2026': 11,
    'ОКТ 2026': 12,
    'НОЯ 2026': 13,
    'ДЕК 2026': 14,
}

# Маппинг: название строки метрики → поле в дашборде
METRIC_ROW_TO_FIELD = {}
for _k in ['Выручка', 'Выручка (тыс руб)', 'Выручка (тыс. руб)', 'Revenue']:
    METRIC_ROW_TO_FIELD[_k] = 'revenue'
for _k in ['EBIT', 'EBIT (тыс руб)', 'EBIT (тыс. руб)']:
    METRIC_ROW_TO_FIELD[_k] = 'ebit'
for _k in ['GP%', 'GP %', 'Gross Profit %', 'Gross Profit']:
    METRIC_ROW_TO_FIELD[_k] = 'gpm'
for _k in ['Cash', 'Cash (тыс руб)', 'Cash (тыс. руб)', 'Кэш', 'Денежные средства']:
    METRIC_ROW_TO_FIELD[_k] = 'cash'
for _k in ['Burn Rate', 'Burn rate', 'Burn Rate (тыс руб)', 'Burn Rate (тыс. руб)', 'Burn']:
    METRIC_ROW_TO_FIELD[_k] = 'burn'
for _k in ['Статус']:
    METRIC_ROW_TO_FIELD[_k] = 'status'
for _k in ['Майлстоуны', 'Milestone', 'Milestones']:
    METRIC_ROW_TO_FIELD[_k] = 'milestone'
for _k in ['Следующие шаги', 'Next steps']:
    METRIC_ROW_TO_FIELD[_k] = 'nextsteps'

CLIENT_STATUS_MAP = {
    'Лид': 'lead', 'Первый контакт': 'lead', '🔵': 'lead',
    'Переговоры': 'neg', '🟡': 'neg',
    'Подписание': 'signed', '🟠': 'signed',
    'Активный': 'active', '🟢': 'active',
    'Отказ': 'lost', '🔴': 'lost',
}

HEADERS = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json',
}


def notion_get(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=30)
    if not r.ok:
        print(f'  [!] Notion API error {r.status_code}: {r.text[:300]}')
        r.raise_for_status()
    return r.json()


def get_blocks(block_id: str) -> list:
    blocks = []
    url = f'https://api.notion.com/v1/blocks/{block_id}/children?page_size=100'
    while url:
        data = notion_get(url)
        blocks.extend(data.get('results', []))
        cursor = data.get('next_cursor')
        url = (
            f'https://api.notion.com/v1/blocks/{block_id}/children?page_size=100&start_cursor={cursor}'
            if cursor else None
        )
    return blocks


def get_text(rich_text: list) -> str:
    return ''.join(t.get('plain_text', '') for t in (rich_text or []))


def get_table_rows(table_id: str) -> list[list[str]]:
    rows = []
    url = f'https://api.notion.com/v1/blocks/{table_id}/children?page_size=100'
    while url:
        data = notion_get(url)
        for block in data.get('results', []):
            if block.get('type') == 'table_row':
                cells = block['table_row'].get('cells', [])
                rows.append([get_text(cell) for cell in cells])
        cursor = data.get('next_cursor')
        url = (
            f'https://api.notion.com/v1/blocks/{table_id}/children?page_size=100&start_cursor={cursor}'
            if cursor else None
        )
    return rows


def normalize_text(s: str) -> str:
    s = (s or '').strip()
    s = s.replace('ё', 'е').replace('Ё', 'Е')
    s = re.sub(r'\s+', ' ', s)
    return s


def month_key_short(s: str) -> str:
    s = normalize_text(s).upper().replace('.', '')
    return s[:3]


def month_key_full(s: str) -> str:
    return normalize_text(s).upper().replace('.', '')


def parse_num(s: str) -> Optional[float]:
    s = normalize_text(s)
    if not s or s in {'—', '-', '–', '—'}:
        return None
    s = s.replace('\u00a0', '').replace(' ', '')
    s = s.replace('тыс.руб', '').replace('тысруб', '').replace('руб.', '').replace('руб', '')
    s = s.replace('%', '')
    s = s.replace(',', '.')
    s = re.sub(r'[^0-9.\-]', '', s)
    if not s or s in {'-', '.', '-.'}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_metrics_table(table_id: str) -> dict:
    rows = get_table_rows(table_id)
    if not rows:
        return {}

    header = rows[0]
    col_to_idx = {}
    for col_i, cell in enumerate(header):
        k = month_key_short(cell)
        if k in MONTH_SHORT_TO_IDX:
            col_to_idx[col_i] = MONTH_SHORT_TO_IDX[k]

    result = {}
    for row in rows[1:]:
        if not row:
            continue
        metric_name = normalize_text(row[0])
        field = METRIC_ROW_TO_FIELD.get(metric_name)
        if not field:
            for key, mapped_field in METRIC_ROW_TO_FIELD.items():
                if key.lower() in metric_name.lower():
                    field = mapped_field
                    break
        if not field:
            continue

        result.setdefault(field, {})
        for col_i, cell in enumerate(row[1:], start=1):
            if col_i not in col_to_idx:
                continue
            mi = col_to_idx[col_i]
            text = normalize_text(cell)
            if field in ('revenue', 'ebit', 'gpm', 'cash', 'burn'):
                val = parse_num(text)
                if val is not None:
                    result[field][mi] = val
            elif field in ('status', 'milestone', 'nextsteps'):
                if text:
                    result[field][mi] = text
    return result


def map_client_status(text: str) -> str:
    for key, code in CLIENT_STATUS_MAP.items():
        if key in text:
            return code
    return 'lead'


def parse_client_month(text: str) -> int:
    k = month_key_full(text)
    if k in MONTH_FULL_TO_IDX:
        return MONTH_FULL_TO_IDX[k]

    short = month_key_short(text)
    if short in MONTH_SHORT_TO_IDX:
        return MONTH_SHORT_TO_IDX[short]

    return 5  # Мар 2026 по умолчанию


def parse_clients_table(table_id: str) -> list:
    rows = get_table_rows(table_id)
    if len(rows) < 2:
        return []

    header = rows[0]
    col_map = {}
    for i, cell in enumerate(header):
        h = normalize_text(cell)
        if 'Клиент' in h or 'Название' in h:
            col_map[i] = 'name'
        elif 'Статус' in h:
            col_map[i] = 'status'
        elif 'Сумма' in h:
            col_map[i] = 'dealSize'
        elif 'Месяц' in h:
            col_map[i] = 'month'
        elif 'Комментарий' in h or 'Коммент' in h:
            col_map[i] = 'comment'

    clients = []
    for row in rows[1:]:
        if not any(normalize_text(c) for c in row):
            continue
        client = {
            'name': '',
            'status': 'lead',
            'dealSize': None,
            'mi': 5,
            'comment': '',
        }
        for col_i, cell in enumerate(row):
            field = col_map.get(col_i)
            if not field:
                continue
            text = normalize_text(cell)
            if field == 'name':
                client['name'] = text
            elif field == 'status':
                client['status'] = map_client_status(text)
            elif field == 'dealSize':
                client['dealSize'] = parse_num(text)
            elif field == 'month':
                client['mi'] = parse_client_month(text)
            elif field == 'comment':
                client['comment'] = text
        if client['name']:
            clients.append(client)

    return clients


def js_str(s) -> str:
    if s is None:
        return 'null'
    s = str(s).replace('\\', '\\\\').replace("'", "\\'").replace('\n', ' ')
    return f"'{s}'"


def js_num(v) -> str:
    if v is None:
        return 'null'
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def generate_js(all_data: dict, sync_ts: str) -> str:
    lines = [f'// Синхронизировано из Notion: {sync_ts}', '']

    for company in COMPANIES:
        data = all_data.get(company)
        if not data:
            continue

        metrics = data.get('metrics', {})
        clients = data.get('clients', [])

        all_months = set()
        for field in ('revenue', 'ebit', 'burn', 'gpm', 'cash', 'status', 'milestone', 'nextsteps'):
            all_months.update(metrics.get(field, {}).keys())

        if all_months:
            c = js_str(company)
            for mi in sorted(all_months):
                parts = []
                rev = metrics.get('revenue', {}).get(mi)
                if rev is not None:
                    parts.append(f'M[{c}][{mi}].revenue={js_num(rev)}')

                ebit = metrics.get('ebit', {}).get(mi)
                if ebit is not None:
                    parts.append(f'M[{c}][{mi}].ebit={js_num(ebit)}')

                burn = metrics.get('burn', {}).get(mi)
                if burn is not None:
                    parts.append(f'M[{c}][{mi}].burn={js_num(burn)}')

                gpm = metrics.get('gpm', {}).get(mi)
                if gpm is not None:
                    parts.append(f'M[{c}][{mi}].gpm={js_num(gpm)}')

                cash = metrics.get('cash', {}).get(mi)
                if cash is not None:
                    parts.append(f'M[{c}][{mi}].cash={js_num(cash)}')

                status = metrics.get('status', {}).get(mi, '')
                if status:
                    parts.append(f'M[{c}][{mi}].status={js_str(status)}')

                ms = metrics.get('milestone', {}).get(mi, '')
                ns = metrics.get('nextsteps', {}).get(mi, '')
                comment = ' | '.join(filter(None, [ms, ns]))
                if comment:
                    parts.append(f'M[{c}][{mi}].comment={js_str(comment)}')

                if parts:
                    lines.append(';'.join(parts) + ';')

        if clients:
            c = js_str(company)
            for cl in clients:
                lines.append(
                    f"MC[{c}].push({{mi:{cl.get('mi', 5)},name:{js_str(cl.get('name', ''))},"
                    f"dealSize:{js_num(cl.get('dealSize'))},status:{js_str(cl.get('status', 'lead'))},"
                    f"comment:{js_str(cl.get('comment', ''))}}});"
                )

        if all_months or clients:
            lines.append('')

    return '\n'.join(lines)


def find_company(title: str) -> Optional[str]:
    title_l = normalize_text(title).lower()
    for c in COMPANIES:
        if c.lower() == title_l:
            return c
        words = [w for w in c.lower().split() if len(w) > 3]
        if any(w in title_l for w in words):
            return c
    return None


def sync():
    if not NOTION_TOKEN:
        print('❌ Ошибка: переменная NOTION_TOKEN не задана.')
        print('   Запусти: NOTION_TOKEN=secret_xxx python3 sync_from_notion.py')
        sys.exit(1)

    print('🔍 Получаем страницы компаний из Notion...')
    blocks = get_blocks(TREKSHON_PAGE_ID)

    company_pages = [
        {'title': b['child_page']['title'], 'id': b['id']}
        for b in blocks
        if b.get('type') == 'child_page'
    ]

    if not company_pages:
        print('⚠️  Страницы компаний не найдены. Проверь ID страницы «Трекшен — по компаниям».')
        sys.exit(1)

    print(f'   Найдено страниц: {len(company_pages)} — {[p["title"] for p in company_pages]}')
    print()

    all_data = {}

    for page in company_pages:
        title = page['title']
        company = find_company(title)
        if not company:
            print(f'   ⏭️  Пропускаем незнакомую страницу: «{title}»')
            continue

        print(f'   📂 Обрабатываем: {company}')
        page_blocks = get_blocks(page['id'])
        tables = [b for b in page_blocks if b.get('type') == 'table']

        metrics_data = {}
        clients_data = []

        if len(tables) >= 1:
            print('      → Таблица метрик...')
            metrics_data = parse_metrics_table(tables[0]['id'])
            filled = sum(len(v) for v in metrics_data.values())
            print(f'         Найдено значений: {filled}')
        else:
            print('      ⚠️  Таблица метрик не найдена')

        if len(tables) >= 2:
            print('      → Таблица клиентов...')
            clients_data = parse_clients_table(tables[1]['id'])
            print(f'         Найдено клиентов: {len(clients_data)}')

        all_data[company] = {'metrics': metrics_data, 'clients': clients_data}
        print()

    now = datetime.now(timezone(timedelta(hours=3)))
    sync_ts = now.strftime('%d.%m.%Y %H:%M МСК')
    js_code = generate_js(all_data, sync_ts)

    if not os.path.exists(HTML_FILE):
        print(f'❌ Файл не найден: {HTML_FILE}')
        sys.exit(1)

    with open(HTML_FILE, 'r', encoding='utf-8') as f:
        html = f.read()

    if '// NOTION_SYNC_START' not in html or '// NOTION_SYNC_END' not in html:
        print('❌ Маркеры // NOTION_SYNC_START и // NOTION_SYNC_END не найдены в HTML.')
        sys.exit(1)

    html = re.sub(r'// NOTION_SYNC_TS=.*', f'// NOTION_SYNC_TS={sync_ts}', html)

    new_block = f'// NOTION_SYNC_START\n{js_code}\n// NOTION_SYNC_END'
    html = re.sub(
        r'// NOTION_SYNC_START.*?// NOTION_SYNC_END',
        new_block,
        html,
        flags=re.DOTALL,
    )

    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'✅ Дашборд обновлён! ({sync_ts})')
    print(f'   Файл: {HTML_FILE}')
    print('   Следующий шаг: закоммить и запушить изменения в GitHub.')


if __name__ == '__main__':
    sync()
