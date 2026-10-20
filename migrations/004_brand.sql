-- Запись бренда (атрибут 85): бренд на Озоне — значение справочника, пишется по dictionary_value_id.
alter table gluing.sku_attr add column if not exists brand_id bigint;      -- dictionary_value_id бренда из живой карточки
alter table gluing.op add column if not exists from_id bigint;             -- было (id справочника), нужно для отката
alter table gluing.op add column if not exists to_id bigint;               -- стало (id справочника)
