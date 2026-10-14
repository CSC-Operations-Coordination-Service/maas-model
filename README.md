# maas-model

This project provides various base classes and DAO code generation from index templates.

## Message Model

### MAASBaseMessage

`MAASBaseMessage` class stores some extra attributes for exchange between MAAS components through the AMQP bus : `ancestor_ids` (list of previous message ids), `message_id` (id of the message), `date`, `pipeline` (List of all engine id crossed by the message since the first ancestor_ids), `force`

### MAASMessage

MAASMessage are based on MAASBaseMessage.
`MAASMessage` class stores some extra attributes for exchange between MAAS components through the AMQP bus : `document_class`, `document_ids`, `document_indices`

The `to_dict()` method outputs a dictionary ready for serialization.

### Custom Message

It is possible to extend MAASBaseMessage, to provide extra information for a custom engine.

```python
@dataclasses.dataclass
class MAASCustomMessage(MAASBaseMessage):

    custom_field: str = "default_value"
```

Next you have to provide the custom class for your custom engine that consume `MAASCustomMessage`
It will allow the engine to deserialize correctly the message (by default `PAYLOAD_MODEL = MAASMessage`)

```python
from maas_model import MAASCustomMessage
from maas_engine.engine.base import Engine

class CustomEngine(Engine):

    PAYLOAD_MODEL = MAASCustomMessage
```

## DAO Code generation

`maas-model` generators output to the standard output, so it is recommended to redirect the output to a file.

### DAO classes

```bash
python -m maas_model.generator.pygen maas-project/resources/*_template.json > maas-project/src/maas_project/model/generated.py
```

Template files starting with `raw-data` will generate classes that inherit `MaasRawDocument` while others will inherit `MaasDocument`

### maas-collector model description

Note it is better to provide only files with `raw-data-` prefix as others may not be useful for the collector.

```bash
python -m maas_model.generator.jsongen maas-project/resources/raw-data-*_template.json > maas-project/charts/collector/conf/maas-project-model.json
```

### FastAPI routers and Cerbos policies

`fastapigen` (needs the `api` extra) writes a Pydantic schema and a FastAPI router per template, plus `routers/__init__.py` aggregating them. With `--policies`, it also writes a Cerbos policy skeleton and an attribute schema per index:

```bash
fastapigen -d maas-project/resources -o maas-project/src/maas_project/generated \
    --package maas_project.generated --document-module maas_project.generated.documents \
    --policies maas-project/cerbos/policies
```

A schema lists the fields its template maps, all optional, and drops any other field. A template whose documents hold fields no mapping can list, such as maas-collector configurations, sets `"_meta": {"free_form": true}` in its mappings: its schema then keeps every field (`extra="allow"`), and a document round-trips whole through the routes. The mapped fields stay the ones Cerbos sees.

Every route asks Cerbos first. The policy of the index decides who may do what, and on which documents: no template setting does. The resource kind is the index name, and there is one action per route:

| Route | Action |
| --- | --- |
| `GET /<index>/` | `<index>:list`. Cerbos plans it, and the plan becomes an OpenSearch filter, so the caller only lists what they may read. |
| `GET /<index>/{id}` | `<index>:read` |
| `POST /<index>/` | `<index>:create`, checked on the body |
| `PUT /<index>/{id}` | `<index>:update`, checked on the stored document and on the new content |
| `DELETE /<index>/{id}` | `<index>:delete` |
| `GET /<index>/_permissions` | the caller's `list` and `create` permissions |
| `GET /<index>/{id}/permissions` | the caller's `read`, `update` and `delete` permissions on that document |
| `GET /permissions` | every resource kind and the actions its routes check |

The service installs its caller and its PDP once:

```python
from maas_model.authz import CerbosAuthorizer, Principal, install_authz

def current_principal(...) -> Principal:  # any FastAPI dependency: a header, a token...
    ...

install_authz(app, principal=current_principal, authorizer=CerbosAuthorizer("http://cerbos:3592"))
```

How the skeleton policies behave:

- They import ocs-portal's `common_roles` derived roles.
- Reads are scoped by the caller's grants (`data_permitted`) when the index maps `mission`, `data_type` or `phase`. Otherwise any portal persona may read.
- Nothing else is allowed until you add a rule. Edit the skeleton freely: `fastapigen` never overwrites a policy, but it regenerates `_schemas/` every time.

When writing conditions:

- **Guard nulls with `R.attr.x != null`, never `has(R.attr.x)`.** Every mapped field is always sent, null when a document has none. The query planner folds `has()` to true, so a list would disagree with a get.
- **Keep list conditions translatable.** Compare keyword, numeric, date or boolean fields with `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `&&`, `||` and `!`. A plan with anything else is refused (403) rather than widened.
- **Relationship rules work in lists too.** `R.attr.assignee == P.id` and `P.id in R.attr.assignees` both translate to OpenSearch filters.

## Inheritance

To override generated classes with inheritance follow this example :

Put `generated.py` file in `maas_cds.model`

Create `__init__.py` file in `maas_cds.model`

```python
"""contains all the model classes"""

# import generated DAO classes
from maas_cds.model.generated import *

# import custom DAO implementations
from maas_cds.model.custom import *
```

The `custom` module contains classes that inherit generated classes

order matter in order to use custom class if any when importing the module

Sample of generated class inheritance :

Create `custom.py` file in `maas_cds.model`

```python
import maas_cds.model.generated as generated

__all__ = ["PripProduct"]


class PripProduct(generated.PripProduct):
    pass

class CdsProduct(generated.CdsProduct):

    def add_data_provider(self, service_type, publication_date):
        if not self.data_providers:
            self.data_providers = []
        data_provider = generated.CdsProductDataProviders()

        data_provider.service_type = service_type
        data_provider.publication_date = publication_date
        #TODO fill other attrs

        self.data_providers.append(data_provider)

```
