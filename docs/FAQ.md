# Dashboard: how it works and FAQ

## How it works

1. **Schedule.** EventBridge Scheduler starts a Lambda at 07:00 and 17:00 Berlin time.
2. **Scan (read-only).** The Lambda lists the resources in `us-east-1` and `eu-central-1`. It covers EC2 instances and volumes, Elastic IPs, public IPv4 addresses, NAT gateways, load balancers, EKS clusters and EKS node groups. It changes and deletes nothing.
3. **Owner.** For each resource, it finds the creation event in CloudTrail. CloudTrail keeps the last 90 days, and the event shows which IAM user created the resource. Some resources take their owner from a parent: an attached volume from its instance, an Elastic IP from its NAT gateway, a load balancer that Kubernetes created from its EKS cluster.
4. **Cohort.** The IAM groups of the cohorts map users to cohorts. A teacher can also upload a roster to the bucket, under `config/roster.csv`, which comes first. A user in neither gets the cohort of the IAM user who created them, if that happened in the last 90 days.
5. **Page.** The Lambda writes one HTML file, `site/index.html`, to a private S3 bucket. You open it from the S3 console after logging into AWS.
6. **Nothing is stored.** Who created what exists only in memory during a run. Each run replaces the page, and old page versions expire after about 1 to 2 days.

All infrastructure is Terraform: `infra/bootstrap` (state bucket and a read-only dev role), `infra/app` (bucket, Lambda, schedule, alarms) and `infra/policies` (the shared read policy). An admin deploys it from their own machine. GitHub never gets AWS credentials.

## FAQ

**Who can see the page?**
Everyone who can log into this AWS account and read the bucket. Students see each other's resources; the teachers agreed to that. The page shows IAM usernames, not real names.

**What does "Check this" mean?**
The resource is older than 4 days, it is a running instance with a public IP, or it is an EKS cluster on an old Kubernetes version. Its row is tinted. Volumes attached to an instance are never marked, because the instance's mark covers them. A mark is a hint, not an order. The teachers decide what has to go.

Old EKS versions are marked because once a version is past standard support, AWS moves the cluster to extended support by default, at $0.60 instead of $0.10 per cluster per hour ([EKS pricing](https://aws.amazon.com/eks/pricing/)). If the version's status can't be read, the cluster is marked too, so a failed lookup never hides it.

Select a resource name to open it in the AWS console (in a new tab).

**Why isn't my resource on my card?**
- It was created more than 90 days ago. CloudTrail no longer has the event, so the resource shows under "No owner found".
- It was created through a shared role. It then shows on a card named `role:<role name>`, marked `unverified`. If the caller set a source identity when taking on the role, it shows under that name instead, also marked `unverified`, because the caller chooses it. The session name is never used, since anyone can type anything there.
- It was created with someone else's credentials, for example Terraform running under another user. It then shows on that user's card.
- It's in a region other than `us-east-1` or `eu-central-1`. Those regions aren't scanned.
- It was created after the last run. Wait for the next one, at 07:00 or 17:00.

**Can I see only the students with something to check?**
Tick "Only show students with something to check" under Cohort. The line above the cards counts the students shown and how many of them have a mark. It follows the cohort too. The box starts unticked on every visit.

**What does "made with an instance that's gone" mean?**
The disk was added when an instance was launched, and the instance has since been terminated while the disk was kept. AWS records no event for such a disk, only the launch, so the dashboard matches the disk to the launch in the same zone within a few seconds. If two people launched at that moment, the disk stays under "No owner found".

**What does "detached from an instance that's gone" mean?**
The disk was detached from an instance, and that instance no longer exists. AWS records the detach and the instance's launch, so the disk goes to whoever launched the instance.

**Where is "No owner found"?**
At the bottom of the full page. The line above the cards links to it ("… with no owner") and to "Cluster gone". "Back to top" at the bottom takes you back. It's hidden while a name is typed into Find me, and shows again under Show everyone.

**What is "Cluster gone"?**
Resources that belonged to an EKS cluster that no longer exists, such as load balancers or volumes left behind. Nobody can be named as the owner. Ask a teacher.

**Does Find me send my username anywhere?**
No. It filters in your browser, and only remembers the name on your device (`localStorage`). Forget me clears it.

**The page has a red banner. What now?**
It means the page is more than 26 hours old: at least two scheduled runs in a row did not update it. Tell a teacher.

**What does the "incomplete" banner mean?**
Part of the scan failed, for example one service in one region. The page may be missing resources. It names what failed.

**What does it cost to run?**
Practically nothing. Lambda and Scheduler stay within the free tier, and the bucket holds a few kB. CloudTrail lookups are free.

**Can the dashboard delete or change anything?**
No. Its role can only read: Describe, List and Get calls, plus CloudTrail lookups. Its one write is the page in its own bucket. A bucket policy also stops anyone else from replacing the page.

**Who gets told when a run fails?**
Three CloudWatch alarms watch for a failed run, no run for about 26 hours, or an incomplete run. They show in the CloudWatch console. Email is off by default. Setting `alarm_email` in `infra/app/terraform.tfvars` and applying turns it on.

**How do I run it locally?**
Without AWS, `python -m dashboard demo` writes a page with fake data to `out-demo/index.html`. The real scan, `python -m dashboard scan`, needs the read-only dev role, which is set up for one maintainer with MFA (see the README). It writes `out/index.html` and `out/verify.md`. Delete `out/` afterwards, because it holds live data.

**How do I deploy a change?**
From a clean checkout of `main`, follow the deploy steps in the README: build, plan, review, apply, smoke test. A person reviews every plan before it is applied, and nothing deploys from GitHub.

**How do I turn it off?**
Either set `schedule_enabled = false` in `infra/app/terraform.tfvars` and apply, which stops the runs, or run `terraform destroy` in `infra/app`, which removes the bucket, the Lambda, the schedule and the alarms. The state bucket and the dev role in `infra/bootstrap` stay. All of it can be rebuilt.
