-- Атрибуты карточек Озона (только чтение с площадки): для проверки «близнецов» и для живого «Названия модели» (атрибут 9048).
create table if not exists gluing.sku_attr (
    account     text   not null,
    offer_id    text   not null,
    product_id  bigint,
    category_id bigint,
    type_id     bigint,
    name        text,
    attrs       jsonb  not null default '{}',     -- {"<attribute_id>": ["значение", ...]}
    fetched_at  timestamptz not null default now(),
    primary key (account, offer_id)
);

-- Какие атрибуты категории Озон считает вариативными («аспектами»).
create table if not exists gluing.category_attr (
    category_id bigint not null,
    type_id     bigint not null,
    attr_id     bigint not null,
    name        text   not null,
    is_aspect   boolean not null,
    is_required boolean not null,
    primary key (category_id, type_id, attr_id)
);

grant all on gluing.sku_attr, gluing.category_attr to gluing_app;
