library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity uart_transceiver is
    port (
        clk         : in    std_logic;
        reset       : in    std_logic;
        tx_data     : in    std_logic_vector(7 downto 0);
        tx_start    : in    std_logic;
        tx_busy     : out   std_logic;
        tx_serial   : out   std_logic;
        rx_serial   : in    std_logic;
        rx_data     : out   std_logic_vector(7 downto 0);
        rx_valid    : out   std_logic;
        baud_sel    : in    std_logic_vector(1 downto 0);
        data_bus    : inout std_logic_vector(7 downto 0)
    );
end entity uart_transceiver;

architecture rtl of uart_transceiver is
begin
    -- Stub architecture
    tx_busy   <= '0';
    tx_serial <= '1';
    rx_data   <= (others => '0');
    rx_valid  <= '0';
    data_bus  <= (others => 'Z');
end architecture rtl;