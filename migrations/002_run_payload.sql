-- Хранить в run то, для чего нет отдельных таблиц: состояние гистерезиса/назначения, проверки, неклеящиеся артикулы.
alter table gluing.run add column if not exists state        jsonb not null default '{}';
alter table gluing.run add column if not exists checks       jsonb not null default '{}';
alter table gluing.run add column if not exists unglued      jsonb not null default '[]';
alter table gluing.run add column if not exists profile_name text  not null default '';
