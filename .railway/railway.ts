import { defineRailway, github, project, service } from "railway/iac";
// This repository manages only its own resources in the environment. Other
// repositories export their own partial name.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
export const partial = "ucuzubu";
export default defineRailway(() => {
  const ucuzubu = service("ucuzubu", {
    source: github("emretuna/ucuzubu", { branch: "main", commitSha: "1802bf9661f2c3aa63ec7446c7492e624e2a2bc4", upstreamUrl: "https://github.com/emretuna/ucuzubu" }),
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "uv run --no-sync python -m ucuzu",
    replicas: { "iad": 1 },
  });

  return project("marvelous-eagerness", {
    variables: { managed: false },
    resources: [ucuzubu],
  });
});
