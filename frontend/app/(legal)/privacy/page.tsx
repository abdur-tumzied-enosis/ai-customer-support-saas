import type { Metadata } from "next";
import Link from "next/link";
import { site } from "@/lib/site";

export const metadata: Metadata = { title: `Privacy Policy · ${site.productName}` };

// Template drafted for launch; have counsel review before go-live (docs/launch-checklist.md).
export default function PrivacyPage() {
	return (
		<>
			<h1>Privacy Policy</h1>
			<p>
				This policy explains how {site.legalName} (&quot;we&quot;, &quot;us&quot;) handles personal data in{" "}
				{site.productName}, an AI assistant that businesses use to answer their customers on websites, Facebook
				Messenger, Instagram and WhatsApp.
			</p>
			<p>There are two groups of people whose data we handle, and our role differs for each:</p>
			<ul>
				<li>
					<strong>Our customers</strong>: businesses and the people on their team who sign up for{" "}
					{site.productName}. For their account data we are the <em>controller</em>.
				</li>
				<li>
					<strong>Their end customers</strong>: people who chat with a business through one of its channels.
					For that data the business is the controller and we are its <em>processor</em>, acting only on its
					instructions under our <Link href="/dpa">Data Processing Agreement</Link>. If you chatted with a
					business, contact that business first; we will help it respond.
				</li>
			</ul>

			<h2>1. Data we collect</h2>
			<h3>Account data</h3>
			<ul>
				<li>Name, email address and password (stored only as a salted hash).</li>
				<li>Project, team membership and settings.</li>
				<li>Technical logs: IP address, request time and path, kept for security and debugging.</li>
			</ul>
			<h3>Business content</h3>
			<ul>
				<li>Documents, web pages and instructions you add to an agent memory.</li>
				<li>Channel credentials (for example Meta page tokens) and API tool settings.</li>
			</ul>
			<h3>End-customer conversations (processed for the business)</h3>
			<ul>
				<li>Messages exchanged with the assistant or the business&apos;s staff.</li>
				<li>
					Identifiers the channel provides, such as a Facebook, Instagram or WhatsApp user ID, name and phone
					number, or for the website widget a random visitor ID.
				</li>
				<li>
					A short contact profile the assistant keeps to help in later conversations (for example name,
					preferred language, order numbers, open issues). It is built only from what the person said and is
					instructed never to keep payment card numbers, passwords, government ID numbers or health details.
				</li>
			</ul>

			<h2>2. How we use it</h2>
			<ul>
				<li>To provide the service: generate replies, search the business&apos;s agent memory, hand conversations to staff.</li>
				<li>To secure the service: prevent abuse, rate-limit, investigate incidents.</li>
				<li>To support you and send service emails (for example password resets). No marketing without consent.</li>
			</ul>
			<p>
				We do not sell personal data and do not use end-customer conversations to train AI models. Our AI providers
				are contracted not to train on data we send them.
			</p>
			<p>
				Legal bases (GDPR): contract for account data; legitimate interest for security and logs; for end-customer
				data, the basis chosen by the business as controller.
			</p>

			<h2>3. AI-generated replies</h2>
			<p>
				Replies are generated automatically by a large language model and may be inaccurate. Businesses can have a
				person take over any conversation, and the website widget tells visitors they are chatting with an AI
				assistant.
			</p>

			<h2>4. Sub-processors</h2>
			<table>
				<thead>
					<tr>
						<th>Provider</th>
						<th>Purpose</th>
					</tr>
				</thead>
				<tbody>
					<tr>
						<td>Google Cloud (Vertex AI / Gemini)</td>
						<td>Generating replies and processing documents</td>
					</tr>
					<tr>
						<td>Meta Platforms (Messenger, Instagram, WhatsApp)</td>
						<td>Delivering messages on channels the business connects</td>
					</tr>
					<tr>
						<td>Hosting provider (to be named)</td>
						<td>Servers, database and backups</td>
					</tr>
					<tr>
						<td>Email provider (to be named)</td>
						<td>Account and password-reset emails</td>
					</tr>
				</tbody>
			</table>
			<p>We will update this list before adding a sub-processor.</p>

			<h2>5. International transfers</h2>
			<p>
				Data may be processed outside your country. Where GDPR applies, transfers rely on the European
				Commission&apos;s Standard Contractual Clauses or an adequacy decision.
			</p>

			<h2>6. Retention</h2>
			<ul>
				<li>Conversations and contact profiles: deleted {site.conversationRetentionDays} days after the last message, or sooner if the business deletes them.</li>
				<li>Agent memory content: until the business deletes it or closes its account.</li>
				<li>Account data: until you delete your account; backups roll off within 30 days.</li>
				<li>Security logs: up to 30 days.</li>
			</ul>

			<h2>7. Your rights</h2>
			<p>
				Depending on where you live you may have the right to access, correct, delete, export or restrict use of
				your data, and to object to processing. Email <a href={`mailto:${site.privacyEmail}`}>{site.privacyEmail}</a>.
				We answer within 30 days. You can also complain to your local data protection authority. See{" "}
				<Link href="/data-deletion">how to delete your data</Link>.
			</p>

			<h2>8. Cookies and local storage</h2>
			<ul>
				<li>Dashboard: a session cookie and a CSRF-protection cookie, both strictly necessary.</li>
				<li>Website widget: a random visitor ID in the browser&apos;s local storage so the conversation continues across pages.</li>
			</ul>
			<p>We use no advertising or cross-site tracking cookies.</p>

			<h2>9. Security</h2>
			<p>
				Traffic is encrypted with TLS, passwords are hashed, and access to production data is limited to staff who
				need it.
			</p>

			<h2>10. Children</h2>
			<p>The dashboard is not for anyone under 16. Businesses must not point the assistant at services aimed at children without their own safeguards.</p>

			<h2>11. Changes</h2>
			<p>We will post changes here and email account owners about material ones before they take effect.</p>

			<h2>12. Contact</h2>
			<p>
				{site.legalName}, {site.address}. Privacy: <a href={`mailto:${site.privacyEmail}`}>{site.privacyEmail}</a>.
			</p>
		</>
	);
}
