"""Read-only checks before enabling the three-credential deployment path."""
import json
import sys

from common import SafeError
from resume_lab import aws


def main():
    identity = aws('sts', 'get-caller-identity')
    account = identity['Account']
    aws('eks', 'describe-cluster', '--name', 'boutique-eq4-eks')
    print('PASS: active Lab session can describe the existing EKS cluster.')
    role_name = identity['Arn'].split(':assumed-role/', 1)[1].split('/', 1)[0]
    role = aws('iam', 'get-role', '--role-name', role_name)['Role']['Arn']
    permissions = aws('iam', 'simulate-principal-policy', '--policy-source-arn', role,
                      '--action-names', 'kms:CreateKey', 'kms:PutKeyPolicy', 'kms:TagResource',
                      'kms:EnableKeyRotation', 'ec2:StartInstances',
                      '--resource-arns', '*')['EvaluationResults']
    print(json.dumps([{'action': p['EvalActionName'], 'decision': p['EvalDecision']}
                      for p in permissions], indent=2))
    print('Account: ' + account)
    print('Simulation is advisory; actual KMS key creation and instance-profile access must also pass.')


if __name__ == '__main__':
    try:
        main()
    except SafeError as exc:
        sys.exit('Automation preflight failed: ' + str(exc))
    except Exception:
        sys.exit('Automation preflight failed; no raw AWS response or credentials were logged.')
