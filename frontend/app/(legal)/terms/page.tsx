import type { Metadata } from "next";
import Link from "next/link";
import { site } from "@/lib/site";

export const metadata: Metadata = {
	title: "Terms of Service",
	description: "The terms for using TumarAgent, the AI customer support assistant for WhatsApp, Messenger and Instagram.",
};

// Template drafted for launch; have counsel review before go-live (docs/launch-checklist.md).
export default function TermsPage() {
	return (
		<>
			<h1>Terms of Service</h1>
			<p>
				These terms are an agreement between you (or the business you represent) and {site.legalName} for use of{" "}
				{site.productName}. By creating an account you accept them, our{" "}
				<Link href="/privacy">Privacy Policy</Link> and, where you process personal data of your customers, our{" "}
				<Link href="/dpa">Data Processing Agreement</Link>.
			</p>

			<h2>1. The service</h2>
			<p>
				{site.productName} lets you build an AI assistant from your own content and connect it to your website and
				messaging channels. During the beta the service is provided free of charge, features may change, and usage
				limits apply. We will give at least 30 days&apos; notice before introducing paid plans; nothing will be
				charged without your agreement.
			</p>

			<h2>2. Your account</h2>
			<ul>
				<li>You must be at least 18 and able to bind the business you sign up for.</li>
				<li>Keep your password secret; you are responsible for activity under your account.</li>
				<li>Give accurate information and keep it up to date.</li>
			</ul>

			<h2>3. Your content and your customers</h2>
			<ul>
				<li>
					You keep all rights to the documents, instructions and conversations you put in the service. You give us
					permission to host and process them only to run the service for you.
				</li>
				<li>
					You are responsible for having the right to upload your content and for giving your end customers any
					notices and obtaining any consents the law requires, including telling them they may be talking to an AI.
				</li>
				<li>You must follow the terms of every channel you connect (for example Meta&apos;s platform and messaging policies).</li>
			</ul>

			<h2>4. Acceptable use</h2>
			<p>You must not use the service to:</p>
			<ul>
				<li>Send spam, unsolicited bulk messages, or messages outside the windows the channel allows.</li>
				<li>Break the law, infringe others&apos; rights, or deceive people, including pretending the assistant is a human when asked.</li>
				<li>Give medical, legal or financial advice that people are expected to rely on without human review.</li>
				<li>Collect payment card data, passwords, government ID numbers or health data through the assistant.</li>
				<li>Harass, threaten, or target children.</li>
				<li>Probe, overload or reverse-engineer the service, or get around usage limits.</li>
			</ul>
			<p>We may suspend accounts that break these rules, with notice where practical.</p>

			<h2>5. AI output</h2>
			<p>
				Replies are generated automatically and can be wrong, incomplete or out of date. You are responsible for
				reviewing how the assistant represents your business, and for any commitments it makes to your customers on
				your behalf. Use human handoff for anything that needs a person.
			</p>

			<h2>6. Availability</h2>
			<p>
				We aim for high availability but do not guarantee the service will be uninterrupted or error-free, especially
				during the beta. Third-party channels and AI providers can be unavailable for reasons outside our control.
			</p>

			<h2>7. Liability</h2>
			<p>
				To the extent the law allows, the service is provided &quot;as is&quot;, and our total liability for any claim
				is limited to the greater of the amounts you paid us in the 12 months before the claim or 100 US dollars. We
				are not liable for indirect or consequential losses, lost profits or lost data. Nothing here limits
				liability that cannot be limited by law.
			</p>

			<h2>8. Ending the agreement</h2>
			<p>
				You can stop using the service and delete your account at any time. We may end the service for you with 30
				days&apos; notice, or immediately for serious breach. After termination we delete your data as described in
				the <Link href="/privacy">Privacy Policy</Link>.
			</p>

			<h2>9. Changes</h2>
			<p>
				We may update these terms. For material changes we will email account owners at least 14 days before they
				take effect; continuing to use the service after that means you accept them.
			</p>

			<h2>10. Law and contact</h2>
			<p>
				These terms are governed by the laws of {site.governingLaw}. Questions:{" "}
				<a href={`mailto:${site.supportEmail}`}>{site.supportEmail}</a>, {site.legalName}, {site.address}.
			</p>
		</>
	);
}
