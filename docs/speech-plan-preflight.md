# Speech plan preflight

In the private admin, open **Planned speech assignments → Check complete speech plan** (`/admin/model_providers/speechselection/preflight/`) to check all four planned English/Polish STT/TTS assignments against a locally approved host policy. This is a static planning check. It does not contact hosts, inspect Docker, download images, change routing, or approve live activation.

The page requires staff access and view permissions for speech assignments, deployments, providers and models/voices. It reports incomplete assignments, incompatible providers, missing artifact/license metadata, unapproved images, architecture mismatches and budget overruns. Shared deployments count once per host, even when both languages reference them. Different deployments of the same provider count separately.

## Local policy

Set `HOTEL_SPEECH_HOST_POLICIES` in the private server environment or Compose `.env`. Compose passes it to the web service. An empty value or `{}` approves no hosts. Invalid JSON or policy fields block the preflight without preventing the reception application from starting. The admin cannot edit this approval policy.

The JSON object maps a deployment's host label to exactly these fields:

| Field | Meaning |
| --- | --- |
| `architecture` | `arm64` or `amd64`; providers declaring `multi` may use either |
| `cpu_limit` | Positive finite JSON number: total CPU allocation reserved for planned speech containers |
| `memory_mib` | Positive JSON integer: total MiB reserved for planned speech containers |
| `approved_images` | List of explicitly approved repository references pinned with `@sha256:` |

For example, this fictional policy reserves one CPU and 1536 MiB for speech on a fixture host. Replace the host, digest and budgets with reviewed values; the digest below is not a usable image:

```dotenv
HOTEL_SPEECH_HOST_POLICIES='{"fixture-host":{"architecture":"arm64","cpu_limit":1,"memory_mib":1536,"approved_images":["example.test/speech@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]}}'
```

Budgets describe the allocation left for speech after reserving resources for Django, the operating system and other services. They are not the host's physical totals. The report sums configured deployment limits, not measured consumption. Each required language needs model/voice metadata with a valid checksum and nonempty license, and the provider needs license metadata. These fields establish metadata completeness only: no artifact hash or legal entitlement is verified by this page.

## Activation remains separate

A passing report is not authorization to activate a plan. It cannot assess current running containers, disk space, old/new overlap during switching, available RAM, readiness, model quality, or concurrent changes. The separate authenticated lifecycle controller must revalidate an execution snapshot, admit the transition against observed resources, serialize changes, drain requests, retain shared deployments, check readiness and restore the old route on failure. Existing Compose services remain authoritative until that controller and live routing are implemented and verified.
