from diagrams import Cluster, Diagram, Edge
from diagrams.aws.storage import S3
from diagrams.aws.database import Dynamodb
from diagrams.aws.security import KMS, IAMRole, CertificateAuthority
from diagrams.aws.management import Organizations, Cloudtrail
from diagrams.aws.cost import Budgets
from diagrams.aws.integration import Eventbridge, SNS
from diagrams.aws.network import VPC, TransitGateway, Endpoint
from diagrams.onprem.vcs import Github
from diagrams.onprem.container import Docker

graph_attr = {
    "fontsize": "16",
    "bgcolor": "white",
    "pad": "0.4",
    "splines": "ortho",
}

with Diagram(
    "ForRad Enterprise AWS Landing Zone & Hybrid Event Fabric",
    filename="docs/diagrams/forrad_hybrid_landing_zone",
    outformat="png",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
):
    with Cluster("Edge & CI/CD Control Planes (Zero Static Keys)"):
        gh_oidc = Github("GitHub Actions OIDC\n(Checkov PaC + Drift)")
        edge_node = Docker("RHEL 10 / Podman\nForRad Edge Broker\n(X.509 Leaf Cert)")

    with Cluster("AWS Organization (o-zu36y3yvr2) — Account 052535487590 (us-east-1)"):
        with Cluster("Layer 1: bootstrap/ (Remote State)"):
            kms = KMS("SSE-KMS CMK")
            tf_s3 = S3("tfstate Bucket\n(TLS 1.2+)")
            tf_lock = Dynamodb("tflocks Table\n(PAY_PER_REQUEST)")
            kms >> tf_s3

        with Cluster("Layer 2: governance/ (Zero-Trust & FinOps)"):
            org = Organizations("4 OUs + 3 SCPs\n(Region / Audit / Zero-Key)")
            budget = Budgets("$5 Cap + $1 Tripwire")
            ca_anchor = CertificateAuthority("Roles Anywhere\nTrust Anchor (Root CA)")
            oidc_role = IAMRole("GitHubActions\nLandingZoneRole")
            eb_drift = Eventbridge("Control-Plane\nDrift Rule")
            sns_alert = SNS("Security Alerts\nbjgarner@uab.edu")
            Cloudtrail("CloudTrail") >> eb_drift >> sns_alert

        with Cluster("Layer 3: network-hub/"):
            tgw = TransitGateway("Supernet 10.100.0.0/16\n(TGW Deferred: $0 Idle)")

        with Cluster("Layer 4: spoke-vending/ (ForRad Dev Spoke 10.100.16.0/20)"):
            spoke_vpc = VPC("Multi-AZ Spoke VPC\n(App /22, DB /24, TGW /28)")
            vpce = Endpoint("Free S3 & DynamoDB\nGateway Endpoints")
            edge_role = IAMRole("ForRadEdgeBroker\nRole-dev (1h STS)")
            ddb_state = Dynamodb("forrad-hybrid-\ntelemetry-state-dev")
            spoke_vpc >> vpce >> ddb_state
    tf_s3 >> Edge(style="invis") >> org >> Edge(style="invis") >> tgw >> Edge(style="invis") >> spoke_vpc

    gh_oidc >> Edge(label="JWT OIDC", color="darkblue") >> oidc_role >> tf_s3
    edge_node >> Edge(label="X.509 mTLS", color="darkgreen") >> ca_anchor >> edge_role >> ddb_state
