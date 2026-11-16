# Speech deployment plans

The private admin can describe intended speech deployments and map English/Polish STT/TTS to them. These are **planned assignments**, not live routing. Saving a plan does not start or stop containers, contact a host, check health, or change the provider used by guests. Compose remains the live configuration until the lifecycle controller is implemented.

Create catalog providers first, then deployment records with a provider, host label and container name. Host labels identify an intended machine; they are not URLs or SSH commands. Container names must be identifiers. Deployment observation starts as **Unknown**, because no controller has inspected the host. Do not interpret this state as running or healthy.

The planned selection view contains four fixed slots: English STT, English TTS, Polish STT and Polish TTS. A slot may remain unassigned. An assigned deployment's provider must support the slot's language and task. The migration creates empty slots and never infers current running containers from environment variables.

Two bilingual deployments can serve all four slots, three deployments can mix sharing with separate language engines, or four deployments can separate each slot. The deployment list shows how many planned slots reference it. This count describes intended ownership only; it is not a count of live requests or running containers.

Referenced deployments/providers are protected from deletion. Clear affected assignments before changing a selected deployment's provider or the selected provider's task/languages. This prevents an edit from silently invalidating another language's plan. Catalog artifact and resource validation remains in effect.

## Remaining lifecycle work

The [static plan preflight](speech-plan-preflight.md) checks metadata, locally approved images, architecture and reserved CPU/RAM allocations before lifecycle work begins. Passing these checks does not authorize activation.

A private authenticated controller must compare plans with observed state, validate approved images and host budgets, serialize changes, drain requests, keep shared providers running while referenced, check replacement readiness and roll back failure. Only after those checks can it atomically update live routing. No such execution or activation action is provided by this planning slice. The remaining hardware, microphone and full-host recovery gates remain open.
