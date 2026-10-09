-- ЧЕРНОВИК. Не применён. Применять на сервере только после согласования (схема в базе marketplace_sync).
-- Схема gluing: планы склеек, их состав, решения, живое состояние площадок, журнал операций, закрепления, аудит.

create schema if not exists gluing;

create table gluing.run (
    run_id         text primary key,                 -- например 2026-10-09-ozon
    platform       text not null check (platform in ('ozon', 'wb')),
    created_at     timestamptz not null default now(),
    engine_version text not null,
    profile_hash   text not null,
    inputs         jsonb not null default '{}',      -- даты снимков остатков, рулонов, окно заказов
    mode           text not null default 'shadow' check (mode in ('shadow', 'manual', 'auto')),
    status         text not null default 'draft'
                   check (status in ('draft', 'approved', 'applying', 'applied', 'partial', 'failed', 'reverted', 'expired')),
    approved_by    text,
    approved_at    timestamptz
);
-- только один несогласованный черновик на площадку
create unique index run_one_draft_per_platform on gluing.run (platform) where status = 'draft';

create table gluing.glue_group (
    run_id     text not null references gluing.run (run_id) on delete cascade,
    group_id   text not null,                        -- стабильный: унаследованное имя модели или g_<хэш>
    key        text not null,                        -- человекочитаемый ключ: кабинет|бренд|тип|ветка#часть
    account    text not null,
    brand      text not null,
    typ        text not null,
    branch     text not null,
    part       int  not null default 1,
    kind       text not null check (kind in ('glue', 'small_glue', 'single_design', 'unglued')),
    n_designs  int  not null,
    n_skus     int  not null,
    target     jsonb not null default '{}',          -- озон: {"model_name": "..."}, wb: {"imt_id": ...}
    reasons    jsonb not null default '[]',
    primary key (run_id, group_id)
);

create table gluing.member (
    run_id       text not null,
    group_id     text not null,
    account      text not null,
    offer_id     text not null,
    design       text not null,
    feature      text,
    stock        int,
    orders_28d   int,
    platform_ids jsonb not null default '{}',        -- product_id, sku (озон) / nm_id (wb)
    manual       boolean not null default false,     -- перенесён вручную
    primary key (run_id, account, offer_id),
    foreign key (run_id, group_id) references gluing.glue_group (run_id, group_id) on delete cascade
);
create index member_by_group on gluing.member (run_id, group_id);

create table gluing.decision (
    id      bigserial primary key,
    run_id  text not null references gluing.run (run_id) on delete cascade,
    node    text not null,                           -- кабинет|бренд|тип
    level   text not null,                           -- признак / детские / эко
    name    text not null,
    n       int  not null,
    result  text not null                            -- и пояснение, и состояние гистерезиса
);
create index decision_by_run on gluing.decision (run_id);

create table gluing.live_state (                     -- что было на площадке до применения
    run_id      text not null references gluing.run (run_id) on delete cascade,
    account     text not null,
    offer_id    text not null,
    model_name  text,
    imt_id      bigint,
    read_at     timestamptz not null default now(),
    primary key (run_id, account, offer_id)
);

create table gluing.op (                             -- журнал применения и основа для отката
    op_id      bigserial primary key,
    run_id     text not null references gluing.run (run_id) on delete cascade,
    account    text not null,
    offer_id   text not null,
    op_type    text not null,                        -- set_model_name / move_nm ...
    from_value text,
    to_value   text,
    status     text not null default 'pending' check (status in ('pending', 'done', 'failed', 'skipped', 'reverted')),
    error      text,
    attempts   int not null default 0,
    applied_at timestamptz
);
create index op_by_run on gluing.op (run_id, status);

create table gluing.pin (                            -- ручные закрепления: дизайн X живёт в группе K
    pin_id     bigserial primary key,
    platform   text not null,
    account    text not null,
    design_uid text not null,                        -- кабинет|бренд|тип|дизайн
    group_key  text not null,
    created_by text not null,
    created_at timestamptz not null default now(),
    expires_at timestamptz,
    reason     text,
    active     boolean not null default true
);
create unique index pin_one_active on gluing.pin (platform, design_uid) where active;

create table gluing.audit (
    id        bigserial primary key,
    at        timestamptz not null default now(),
    user_name text not null,
    action    text not null,
    run_id    text,
    details   jsonb not null default '{}'
);

-- Роль: чтение public (данные db_sync), полный доступ только к gluing. Выполняет администратор, пароль не хранится в репозитории.
-- create role gluing_app login password '<задаётся на сервере>';
-- grant usage on schema public to gluing_app;
-- grant select on all tables in schema public to gluing_app;
-- grant all on schema gluing to gluing_app;
-- grant all on all tables in schema gluing to gluing_app;
-- grant all on all sequences in schema gluing to gluing_app;
