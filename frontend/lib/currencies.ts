export type Currency = { code: string; symbol: string; name: string };

// BDT first: it is the default for new projects
export const CURRENCIES: Currency[] = [
	{ code: "BDT", symbol: "৳", name: "Bangladeshi taka" },
	{ code: "USD", symbol: "$", name: "US dollar" },
	{ code: "EUR", symbol: "€", name: "Euro" },
	{ code: "GBP", symbol: "£", name: "British pound" },
	{ code: "INR", symbol: "₹", name: "Indian rupee" },
	{ code: "PKR", symbol: "₨", name: "Pakistani rupee" },
	{ code: "AED", symbol: "AED", name: "UAE dirham" },
	{ code: "SAR", symbol: "SAR", name: "Saudi riyal" },
	{ code: "MYR", symbol: "RM", name: "Malaysian ringgit" },
	{ code: "SGD", symbol: "S$", name: "Singapore dollar" },
	{ code: "CAD", symbol: "C$", name: "Canadian dollar" },
	{ code: "AUD", symbol: "A$", name: "Australian dollar" },
	{ code: "JPY", symbol: "¥", name: "Japanese yen" },
	{ code: "CNY", symbol: "CN¥", name: "Chinese yuan" },
	{ code: "TRY", symbol: "₺", name: "Turkish lira" },
	{ code: "NGN", symbol: "₦", name: "Nigerian naira" },
];
