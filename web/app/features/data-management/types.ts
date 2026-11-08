export type DataTab = "market" | "operations" | "monitor";

export type DataFilters = {
  accountId: string;
  eventType: string;
  symbol: string;
  start: string;
  end: string;
};

export const emptyDataFilters: DataFilters = {
  accountId: "",
  eventType: "",
  symbol: "",
  start: "",
  end: "",
};
