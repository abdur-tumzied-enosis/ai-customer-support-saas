import type { Metadata } from "next";
import Link from "next/link";
import { site } from "@/lib/site";

export const metadata: Metadata = { title: `Data Deletion · ${site.productName}` };

// Also the "Data Deletion Instructions URL" in the Meta app settings.
export default function DataDeletionPage() {
	const mail = `mailto:${site.privacyEmail}?subject=${encodeURIComponent("Data deletion request")}`;
	return (
		<>
			<h1>Delete your data</h1>
			<p>
				How to delete personal data held by {site.productName}. Full details are in our{" "}
				<Link href="/privacy">Privacy Policy</Link>.
			</p>

			<h2>If you messaged a business</h2>
			<p>
				If you chatted with a business on Facebook Messenger, Instagram, WhatsApp or its website, that business
				controls your conversation. You can ask it directly, or email{" "}
				<a href={mail}>{site.privacyEmail}</a> with:
			</p>
			<ul>
				<li>the name of the business and the channel you used;</li>
				<li>
					your name as shown on that channel, or your phone number for WhatsApp, or for a website chat the date and
					approximate time;
				</li>
				<li>what you want deleted (everything, or specific conversations).</li>
			</ul>
			<p>
				We confirm the request with the business and delete your messages and contact profile within 30 days. You
				will get an email when it is done.
			</p>

			<h2>If you removed our app from Facebook or Instagram</h2>
			<p>
				Removing the app stops new messages from reaching us. To also delete what we already hold, send a request as
				described above.
			</p>

			<h2>If you have a {site.productName} account</h2>
			<p>
				Sign in and open <strong>Account settings → Delete account</strong>. This immediately deletes your account and
				every project only you belong to, with its agent memories, documents, widgets and conversations; backups
				roll off within 30 days. The same page has <strong>Export data</strong> if you want a copy first. Can&apos;t
				sign in? Email <a href={mail}>{site.privacyEmail}</a> from the address on your account.
			</p>
			<p>Conversations are also deleted automatically {site.conversationRetentionDays} days after the last message.</p>
		</>
	);
}
