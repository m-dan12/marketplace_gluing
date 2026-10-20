-- Живое состояние карточек WB: в какой склейке (imtID) карточка лежит сейчас. Только чтение с площадки (content-api).
-- Применять на сервере только после согласования; схема gluing, роль gluing_app.
create table if not exists gluing.wb_card (
    account     text   not null,
    nm_id       bigint not null,
    vendor_code text   not null,
    imt_id      bigint not null,
    subject     text,
    brand       text,
    title       text,
    color       text,                    -- характеристика «Цвет» (для проверки дублей цвета в склейке)
    fetched_at  timestamptz not null default now(),
    primary key (account, nm_id)
);
create index if not exists wb_card_by_imt on gluing.wb_card (account, imt_id);

grant all on gluing.wb_card to gluing_app;
