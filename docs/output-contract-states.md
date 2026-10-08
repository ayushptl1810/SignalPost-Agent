# Discovery state mapping

The cache and live-discovery report use these states for candidate families.
They describe what was observed, not a negative assertion about the company.

| State | Meaning |
| --- | --- |
| `available` | A candidate passed the identity and first-party gate and was published. |
| `ambiguous` | At least one candidate loaded and carried entity/name/address or registry evidence, but did not pass the publication gate. |
| `not_available` | Every candidate failed DNS resolution, returned 404/410, or loaded without entity evidence. |
| `blocked` | Robots or the outbound URL policy blocked every otherwise promising candidate. |
| `failed` | A resolved candidate that could have been promising failed with timeout, TLS, or 5xx; this is distinct from an observed absence. |
| `not_checked` | The source was not run, for example because a NAV index was absent or stale. |

The official batch maps these internal states to terminal envelope evidence without
turning an unchecked or ambiguous result into a published claim: `available` is
`complete`, `not_available` and `ambiguous` are `not_found`, `blocked` is a
policy/robots block, and `failed` is `source_error`.
