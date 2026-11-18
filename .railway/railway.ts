import { defineRailway, github, project, service } from "railway/iac";
// This repository manages only its own resources in the environment. Other
// repositories export their own partial name.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
export const partial = "ucuzubu";
export default defineRailway(() => {
  const ucuzubu = service("ucuzubu", {
    source: github("emretuna/ucuzubu", { branch: "main", commitSha: "dd17f9f6a4895337931128e649840e1b5e55e36b", upstreamUrl: "https://github.com/emretuna/ucuzubu" }),
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "uv run --no-sync python -m ucuzu",
    replicas: { "iad": 1 },
  });

  return project("marvelous-eagerness", {
    variables: { managed: false },
    resources: [ucuzubu],
  });
});
