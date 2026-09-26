import { BarChart3, Bell, ClipboardList, Container, FileSearch, History, LayoutDashboard, LockKeyhole, Receipt, Tags, Upload, type LucideIcon } from "lucide-react";

export type NavKey = "overview" | "reports" | "skus" | "periods" | "costAudit" | "containers" | "purchaseOrders" | "invoices" | "alerts" | "imports" | "audit";

export type NavItem = {
  href: string;
  key: NavKey;
  icon: LucideIcon;
  match: (pathname: string) => boolean;
  badge?: boolean;
};

export type NavGroup = { key: "groupSteer" | "groupFiles" | "groupData"; items: NavItem[] };

const under = (root: string) => (p: string) => p === root || p.startsWith(`${root}/`);

/**
 * Three questions, three groups: how are my imports doing (steer), which file am I working on
 * (files), where does the data come from and who touched it (data). Settings and help sit apart, at
 * the foot of the sidebar.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    key: "groupSteer",
    items: [
      { href: "/overview", key: "overview", icon: LayoutDashboard, match: under("/overview") },
      { href: "/reports", key: "reports", icon: BarChart3, match: under("/reports") },
      { href: "/skus", key: "skus", icon: Tags, match: under("/skus") },
      { href: "/periods", key: "periods", icon: LockKeyhole, match: under("/periods") },
      { href: "/cost-audit", key: "costAudit", icon: FileSearch, match: under("/cost-audit") },
    ],
  },
  {
    key: "groupFiles",
    items: [
      { href: "/containers", key: "containers", icon: Container, match: (p) => p === "/containers" || p.startsWith("/container/") },
      { href: "/purchase-orders", key: "purchaseOrders", icon: ClipboardList, match: under("/purchase-orders") },
      { href: "/invoices", key: "invoices", icon: Receipt, match: under("/invoices") },
      { href: "/alerts", key: "alerts", icon: Bell, match: under("/alerts"), badge: true },
    ],
  },
  {
    key: "groupData",
    items: [
      { href: "/imports", key: "imports", icon: Upload, match: under("/imports") },
      { href: "/audit", key: "audit", icon: History, match: under("/audit") },
    ],
  },
];
