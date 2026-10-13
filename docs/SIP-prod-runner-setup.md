# AWS prod deploy — in-cluster runner bring-up

How to bring the AWS-prod deploy path online. The prod EKS cluster (`c-5zsl4`) is
reachable only from inside the dev EKS pod network (the nonprod EC2 is firewalled
off the prod Rancher), so the prod deploy runs on a self-hosted GitHub Actions
runner **deployed as a pod in the dev EKS cluster** (`deploy/runner/eks-prod-runner.yaml`).

## Flow it enables
```
PR -> merge main -> CI builds -> dev auto-deploys (EC2) -> you validate
    -> promote `prod` branch -> [approve: production env] -> prod EKS + Azure collector deploy
```

## One-time setup

### 1. Two secrets in the dev namespace (never in git)
Run on the EC2 (`KUBECONFIG` = dev), substituting real values:

```bash
# (a) prod cluster kubeconfig — download from the prod Rancher UI for c-5zsl4
kubectl --context dev -n storage-mgt-tools-na-dev \
  create secret generic prod-kubeconfig --from-file=config=<path-to-prod-kubeconfig>

# (b) fine-grained GitHub PAT: repo apythoneer/app-usm,
#     permission "Self-hosted runners: Read and write". Paste at the prompt:
read -rsp "PAT: " PAT; echo
kubectl --context dev -n storage-mgt-tools-na-dev \
  create secret generic gh-runner-pat --from-literal=token="$PAT"; unset PAT
```

### 2. Deploy the runner
```bash
kubectl --context dev apply -f deploy/runner/eks-prod-runner.yaml
kubectl --context dev -n storage-mgt-tools-na-dev rollout status deploy/gha-runner-eks-prod
```
It should appear under repo **Settings -> Actions -> Runners** as `eks-prod-*` (labels `self-hosted, eks-prod`).

### 3. Gate the `production` environment
Repo **Settings -> Environments -> `production` -> Required reviewers** -> add the approver(s).
(The `deploy-prod.yml` job declares `environment: production`; until a reviewer is set it won't actually pause.)

## Verify the path (before trusting a real promotion)
```bash
# the runner pod can reach prod Rancher + prod cluster:
kubectl --context dev -n storage-mgt-tools-na-dev exec deploy/gha-runner-eks-prod -- \
  sh -c 'KUBECONFIG=/opt/prod-kube/config /tools/kubectl get ns storage-mgt-tools-na'
```
A namespace listing = the runner can deploy to prod. (Confirm the prod namespace
name on `c-5zsl4`; the overlay assumes `storage-mgt-tools-na`.)

## Notes
- `prod-kubeconfig` is mounted `optional: true`, so the runner registers and shows
  online before the kubeconfig exists; deploys just fail until it is added.
- The PAT (not an ephemeral registration token) lets the runner re-register itself
  across pod restarts.
- `deploy-azure-collector.yml` is the sibling path (AKS, `azure-prod` runner on the
  Azure VM) and fires on the same `prod` promotion.
