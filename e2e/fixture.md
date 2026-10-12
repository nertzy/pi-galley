ALPHA paragraph. A notification starts when the API receives a trigger request with a workflow id, a subscriber id and a payload. The pipeline validates the payload, loads the subscriber and resolves which steps run.

```mermaid
flowchart LR
  A[Request received] --> B[Validate payload] --> C[Load subscriber] --> D[Resolve workflow] --> E[Render templates] --> F[Send to provider] --> G[Record delivery]
```

BRAVO paragraph. Each channel step renders its templates with the payload and subscriber data, hands the result to a provider, and records the outcome so you can trace a missing notification back to the stage where it stopped.

```mermaid
flowchart LR
  S[SmallStart] --> T[SmallEnd]
```

- LIST item with a nested wide diagram that Pi leaves as source:

  ```mermaid
  flowchart LR
    N1[Nested one] --> N2[Nested two] --> N3[Nested three] --> N4[Nested four] --> N5[Nested five] --> N6[Nested six] --> N7[Nested seven]
  ```

````markdown
```mermaid
flowchart LR
  X1[Example one] --> X2[Example two] --> X3[Example three] --> X4[Example four] --> X5[Example five] --> X6[Example six] --> X7[Example seven]
```
````

CHARLIE paragraph. This closing paragraph comes after every diagram and example block, and it must wrap back inside the column exactly like the ALPHA and BRAVO paragraphs above it.
