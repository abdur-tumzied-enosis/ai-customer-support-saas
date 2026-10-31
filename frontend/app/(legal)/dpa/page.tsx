import type { Metadata } from "next";
import Link from "next/link";
import { site } from "@/lib/site";

export const metadata: Metadata = { title: `Data Processing Agreement · ${site.productName}` };

// Template drafted for launch; have counsel review before go-live (docs/launch-checklist.md).
export default function DpaPage() {
	return (
		<>
			<h1>Data Processing Agreement</h1>
			<p>
				This agreement forms part of the <Link href="/terms">Terms of Service</Link> between the customer
				(&quot;Controller&quot;) and {site.legalName} (&quot;Processor&quot;) and applies whenever we process personal
				data of the Controller&apos;s end customers under the EU/UK GDPR or similar laws. Need a signed copy? Email{" "}
				<a href={`mailto:${site.privacyEmail}`}>{site.privacyEmail}</a>.
			</p>

			<h2>1. Scope of processing</h2>
			<table>
				<tbody>
					<tr>
						<th>Subject matter</th>
						<td>Answering the Controller&apos;s end customers on the channels it connects.</td>
					</tr>
					<tr>
						<th>Duration</th>
						<td>For the term of the agreement, plus the deletion period below.</td>
					</tr>
					<tr>
						<th>Data subjects</th>
						<td>The Controller&apos;s end customers and website visitors; the Controller&apos;s staff.</td>
					</tr>
					<tr>
						<th>Data categories</th>
						<td>Messages, channel identifiers (name, user ID, phone number), contact profile facts, website visitor IDs.</td>
					</tr>
					<tr>
						<th>Special categories</th>
						<td>None intended. The Controller must not use the service to collect them.</td>
					</tr>
				</tbody>
			</table>

			<h2>2. Processor obligations</h2>
			<ul>
				<li>Process personal data only on the Controller&apos;s documented instructions, including these terms and its settings in the dashboard.</li>
				<li>Ensure staff with access are bound by confidentiality.</li>
				<li>Apply appropriate technical and organisational security measures (Article 32), including encryption in transit, access control and backups.</li>
				<li>Help the Controller answer data subject requests and carry out impact assessments where needed.</li>
				<li>Notify the Controller without undue delay, and within 48 hours, after becoming aware of a personal data breach.</li>
				<li>Delete or return personal data at the end of the service, unless the law requires keeping it.</li>
				<li>Make available the information needed to demonstrate compliance and allow reasonable audits.</li>
			</ul>

			<h2>3. Sub-processors</h2>
			<p>
				The Controller authorises the sub-processors listed in our <Link href="/privacy">Privacy Policy</Link>. We
				will give at least 14 days&apos; notice of a new sub-processor by email; the Controller may object on reasonable
				data protection grounds and, if we cannot resolve the objection, end the agreement. We impose the same data
				protection obligations on each sub-processor and remain responsible for them.
			</p>

			<h2>4. International transfers</h2>
			<p>
				Transfers outside the EEA/UK rely on an adequacy decision or the European Commission&apos;s Standard
				Contractual Clauses (Module 2 or 3 as applicable), which are incorporated by reference.
			</p>

			<h2>5. Deletion</h2>
			<p>
				Conversations are deleted {site.conversationRetentionDays} days after the last message by default. When the
				Controller closes its account, we delete its data within 30 days and from backups within a further 30 days.
			</p>
		</>
	);
}
