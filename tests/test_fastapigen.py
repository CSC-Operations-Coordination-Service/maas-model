import json

import pytest

from maas_model.generator.fastapigen import SchemaGenerator
from maas_model.generator.meta import FieldMeta, ModelClassMeta


def _write_processor_template(path):
	template = {
		"aliases": {"processor": {}},
		"index_patterns": ["processor-*"],
		"settings": {
			"index": {
				"number_of_shards": 1,
				"number_of_replicas": 1,
			}
		},
		"mappings": {
			"properties": {
				"id": {"type": "text"},
				"mission": {"type": "keyword"},
				"processing_baseline": {"type": "text"},
				"release_notes": {"type": "keyword"},
				"satellite_units": {"type": "keyword"},
				"target_ipfs": {"type": "keyword"},
				"priority": {"type": "integer"},
				"retry_count": {"type": "long"},
				"score": {"type": "float"},
				"error_rate": {"type": "double"},
				"enabled": {"type": "boolean"},
				"release_date": {"type": "date"},
				"validity_end_date": {"type": "date"},
				"validity_start_date": {"type": "date"},
				"source_ip": {"type": "ip"},
			}
		},
	}
	path.write_text(json.dumps(template), encoding="UTF-8")


def test_fastapi_schema_generator_with_processor_template(tmp_path):
	tpl = tmp_path / "processor_template.json"
	_write_processor_template(tpl)

	meta = ModelClassMeta(str(tpl))
	meta.load()

	generator = SchemaGenerator(meta)
	generated_source = generator.generate()

	assert meta.class_name == "Processor"
	assert "from pydantic import BaseModel" in generated_source
	assert "from datetime import datetime" in generated_source
	assert "from pydantic import IPvAnyAddress" in generated_source

	assert "class Processor(BaseModel):" in generated_source

	# keyword/text -> str
	assert "    id: str" in generated_source
	assert "    mission: str" in generated_source
	assert "    processing_baseline: str" in generated_source
	assert "    release_notes: str" in generated_source
	assert "    satellite_units: str" in generated_source
	assert "    target_ipfs: str" in generated_source
	assert "    source_ip: IPvAnyAddress" in generated_source

	# integer/long -> int
	assert "    priority: int" in generated_source
	assert "    retry_count: int" in generated_source

	# float/double -> float
	assert "    score: float" in generated_source
	assert "    error_rate: float" in generated_source

	# boolean -> bool
	assert "    enabled: bool" in generated_source

	# date -> datetime
	assert "    release_date: datetime" in generated_source
	assert "    validity_end_date: datetime" in generated_source
	assert "    validity_start_date: datetime" in generated_source

    


def test_fastapi_schema_generator_unsupported_type_raises_value_error():
	meta = ModelClassMeta("processor_template.json")
	meta.fields = [FieldMeta(name="footprint", type_name="GeoShape", properties=[])]

	with pytest.raises(ValueError, match="Unsupported field type"):
		SchemaGenerator(meta).generate()


def test_fastapi_schema_generator_nested_object_raises_value_error(tmp_path):
	tpl = tmp_path / "processor_template.json"
	_write_processor_template(tpl)

	meta = ModelClassMeta(str(tpl))
	meta.load()
	meta.fields.append(
		FieldMeta(
			name="metadata",
			type_name="Object",
			properties=[FieldMeta(name="source", type_name="Keyword", properties=[])],
		)
	)

	with pytest.raises(ValueError, match="Nested object fields are not supported"):
		SchemaGenerator(meta).generate()
